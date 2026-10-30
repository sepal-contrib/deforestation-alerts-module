"""Characterization tests for component/scripts/report_builder.py pure helpers."""

import base64
from pathlib import Path

import pytest

from deforestation_alerts.scripts.report_builder import (
    get_unique_alerts,
    format_list,
    parse_formatted_string,
)


# Smallest valid JPEG, so python-docx's ``add_picture`` accepts the stubbed
# renders below instead of choking on empty files.
_JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, {"GLAD-L"}),          # ones digit
        (10, {"RADD"}),           # tens digit
        (100, {"GLAD-S2"}),       # hundreds digit
        (1000, {"CCDC"}),         # thousands digit
        (1111, {"GLAD-L", "RADD", "GLAD-S2", "CCDC"}),  # all sources, code 1
        (2222, {"GLAD-L", "RADD", "GLAD-S2", "CCDC"}),  # all sources, code 2
        (0, set()),               # no source triggered
    ],
)
def test_get_unique_alerts_decodes_digit_encoding(value, expected):
    assert set(get_unique_alerts([value])) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (10000, {"MyAlerts"}),                       # slot 0, code 1
        (20000, {"MyAlerts"}),                       # slot 0, code 2
        (10001, {"GLAD-L", "MyAlerts"}),             # union with GLAD-L
        (11000, {"CCDC", "MyAlerts"}),               # CCDC stays position-bounded
        (1, {"GLAD-L"}),                             # custom digits empty
        (100000, {"Sentinel"}),                      # slot 1
        (200000020, {"RADD", "Planet"}),             # slot 4 code 2 + RADD code 2
        (210010, {"RADD", "MyAlerts", "Sentinel"}),  # RADD + slot 0 (1) + slot 1 (2)
        (1010010, {"RADD", "MyAlerts", "Other"}),    # RADD + slot 0 + slot 2
    ],
)
def test_get_unique_alerts_decodes_custom_slots(value, expected):
    names = {0: "MyAlerts", 1: "Sentinel", 2: "Other", 4: "Planet"}
    assert set(get_unique_alerts([value], custom_names=names)) == expected


def test_get_unique_alerts_unnamed_slot_falls_back_to_numbered_label():
    assert set(get_unique_alerts([10000])) == {"Custom 1"}
    assert set(get_unique_alerts([1000000], custom_names={0: "A"})) == {"Custom 3"}


@pytest.mark.parametrize("value", [10000.0, "10000", "10000.0", "[10000.0]"])
def test_get_unique_alerts_accepts_float_codes(value):
    # The float64 export and CSV round-trips hand codes over as floats/strings.
    arg = value if isinstance(value, str) and value.startswith("[") else [value]
    assert set(get_unique_alerts(arg, custom_names={0: "A"})) == {"A"}


def test_get_unique_alerts_accepts_stringified_list():
    # Recipe storage round-trips this column as a string; the decoder must parse it.
    assert set(get_unique_alerts("[1, 10]")) == {"GLAD-L", "RADD"}


def test_get_unique_alerts_invalid_string_returns_empty():
    assert get_unique_alerts("not a list") == []


def test_digit_map_decides_names():
    assert set(get_unique_alerts([21], digit_map={0: "GLAD-L", 1: "RADD"})) == {"GLAD-L", "RADD"}
    assert set(get_unique_alerts([1000], digit_map={3: "My CCDC"})) == {"My CCDC"}


def test_an_empty_digit_map_names_nothing():
    """An alert set's map decides even when empty: no fall-back to the fixed layout."""
    assert get_unique_alerts([21], digit_map={}) == []


def test_digit_map_nine_digits():
    names = {d: f"s{d}" for d in range(9)}
    assert len(get_unique_alerts([222222222], digit_map=names)) == 9


def test_format_list_join_rules():
    assert format_list([]) == ""
    assert format_list(["GLAD-L"]) == "GLAD-L"
    assert format_list(["GLAD-L", "RADD"]) == "GLAD-L and RADD"
    assert format_list(["GLAD-L", "RADD", "CCDC"]) == "GLAD-L, RADD and CCDC"


def test_parse_formatted_string_is_inverse_of_format_list():
    for items in (["GLAD-L"], ["GLAD-L", "RADD"], ["GLAD-L", "RADD", "CCDC"]):
        assert parse_formatted_string(format_list(items)) == items
    assert parse_formatted_string("") == []


@pytest.mark.parametrize("epsg, lat", [(3857, 0.0), (3857, 60.0), (32721, None)])
def test_scale_bar_reads_crs_metres(tmp_path, monkeypatch, epsg, lat):
    """The extent is in CRS metres, so dx is 1 m per unit; Web Mercator is
    corrected to ground metres at the image centre (cos(lat))."""
    import math

    import numpy as np
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin

    from deforestation_alerts.scripts import report_builder

    if lat is None:  # a UTM image: plain metres
        x, y = 500000.0, 7000000.0
    else:
        x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(-56.0, lat)
    tif = tmp_path / "img.tif"
    with rasterio.open(
        tif, "w", driver="GTiff", width=10, height=10, count=3, dtype="uint8",
        crs=f"EPSG:{epsg}", transform=from_origin(x - 50, y + 50, 10, 10),
    ) as dst:
        dst.write(np.zeros((3, 10, 10), dtype="uint8"))

    bars = []

    class _Spy(report_builder.ScaleBar):
        def __init__(self, dx, *args, **kwargs):
            bars.append(dx)
            super().__init__(dx, *args, **kwargs)

    monkeypatch.setattr(report_builder, "ScaleBar", _Spy)
    report_builder.plot_tiff_with_overlay(str(tif), tmp_path / "out.jpg")

    expected = 1.0 if lat is None else math.cos(math.radians(lat))
    assert bars == [pytest.approx(expected, abs=1e-4)]


# ---------------------------------------------------------------------------
# The report is USER DATA: it must land on its output path (the set folder),
# and the intermediates it needs live in a private scratch folder.
# ---------------------------------------------------------------------------


def _alert_gpkg(tmp_path):
    """One-row GPKG with the columns the report reads (what export.py writes)."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {
            "admin1": ["Paraguay"], "admin2": ["Canindeyú"], "admin3": ["Curuguaty"],
            "alert_date_min": [2026.001], "alert_date_max": [2026.010],
            "before_img": ["S2_before"], "after_img": ["S2_after"],
            "alert_sources": ["GLAD-L"], "area_ha": [1.25],
        },
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326",
    )
    path = tmp_path / "alert_0.gpkg"
    gdf.to_file(path, driver="GPKG")
    return str(path)


@pytest.fixture()
def stub_plots(monkeypatch, tmp_path):
    """Replace the cartopy/matplotlib figure render with a byte write.

    The six JPEGs are only interesting here as *files in the scratch dir the
    report builder creates*; rendering them for real would make this a slow
    plotting test instead of a path-contract one. The stub records the paths so
    the test can prove they were inside that scratch dir.
    """
    from deforestation_alerts.scripts import report_builder

    seen = []

    def _fake_plot(tiff_path, output_path, **kwargs):
        seen.append(Path(output_path))
        Path(output_path).write_bytes(_JPEG_1PX)

    monkeypatch.setattr(report_builder, "plot_tiff_with_overlay", _fake_plot)
    return seen


@pytest.fixture()
def scratch_root(monkeypatch, tmp_path):
    """The process scratch root, pointed into ``tmp_path``."""
    from deforestation_alerts import gdal_env

    root = tmp_path / "scratch"
    monkeypatch.setattr(gdal_env, "scratch_dir", lambda: root)
    return root


def test_report_is_saved_onto_its_output_path(data_root, tmp_path, scratch_root, stub_plots):
    from deforestation_alerts.scripts.report_builder import (
        generate_deforestation_report_with_word_template,
    )
    from gui.tile.analysis.alert_files import DEFAULT_REPORT_TEMPLATE as _DEFAULT_REPORT_TEMPLATE

    out = str(data_root / "recipe_a" / "alert_0.docx")

    written = generate_deforestation_report_with_word_template(
        image_path=str(tmp_path / "unused.tif"),
        geodataframe_path=_alert_gpkg(tmp_path),
        template_path=_DEFAULT_REPORT_TEMPLATE,
        output_path=out,
    )

    assert written == out
    # A real .docx (a zip container) landed in the set folder, with no .part...
    assert Path(out).read_bytes()[:2] == b"PK"
    assert [p.name for p in (data_root / "recipe_a").iterdir()] == ["alert_0.docx"]
    # ...and its scratch folder is gone.
    assert list(scratch_root.iterdir()) == []


def test_report_intermediates_go_to_a_private_scratch_dir(data_root, tmp_path, scratch_root, stub_plots):
    """The six JPEGs used to share one ``<module_dir>/temp``, so two concurrent
    reports overwrote each other's ``output_image1.jpg``."""
    from deforestation_alerts.scripts.report_builder import (
        generate_deforestation_report_with_word_template,
    )
    from gui.tile.analysis.alert_files import DEFAULT_REPORT_TEMPLATE as _DEFAULT_REPORT_TEMPLATE

    gpkg = _alert_gpkg(tmp_path)
    for name in ("alert_0.docx", "alert_1.docx"):
        generate_deforestation_report_with_word_template(
            image_path=str(tmp_path / "unused.tif"),
            geodataframe_path=gpkg,
            template_path=_DEFAULT_REPORT_TEMPLATE,
            output_path=str(data_root / "recipe_a" / name),
        )

    assert len(stub_plots) == 12
    assert {p.name for p in stub_plots} == {
        f"output_image{i}.jpg" for i in range(1, 7)
    }
    # 12 renders, 12 distinct paths: the two runs never shared a directory.
    assert len(set(stub_plots)) == 12
    for path in stub_plots:
        assert path.is_relative_to(scratch_root)


def test_get_unique_alerts_lists_the_sources_in_digit_order():
    # alert_sources is stored text: the same code must give the same text.
    assert get_unique_alerts([1011]) == ["GLAD-L", "RADD", "CCDC"]
    assert get_unique_alerts([21, 10000], custom_names={0: "Mine"}) == ["GLAD-L", "RADD", "Mine"]
    assert get_unique_alerts([12], digit_map={1: "B", 0: "A"}) == ["A", "B"]
