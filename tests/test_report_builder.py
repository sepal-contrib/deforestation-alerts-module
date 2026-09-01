"""Characterization tests for component/scripts/report_builder.py pure helpers."""

import base64
from pathlib import Path

import pytest

from deforestation_alerts.scripts.report_builder import (
    get_unique_alerts,
    format_list,
    parse_formatted_string,
    ensure_list,
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
    "value, custom_name, expected",
    [
        (10000, "MyAlerts", {"MyAlerts"}),                 # ten-thousands, code 1
        (20000, "MyAlerts", {"MyAlerts"}),                 # ten-thousands, code 2
        (10001, "MyAlerts", {"GLAD-L", "MyAlerts"}),       # union with GLAD-L
        (11000, "MyAlerts", {"CCDC", "MyAlerts"}),         # union w/ CCDC (position-bounded)
        (1, "MyAlerts", {"GLAD-L"}),                       # custom slot empty -> name absent
    ],
)
def test_get_unique_alerts_decodes_custom_tenthousands(value, custom_name, expected):
    assert set(get_unique_alerts([value], custom_name=custom_name)) == expected


def test_get_unique_alerts_custom_defaults_to_generic_label():
    # No name supplied -> a generic "Custom" label rather than nothing.
    assert set(get_unique_alerts([10000])) == {"Custom"}


def test_get_unique_alerts_accepts_stringified_list():
    # Recipe storage round-trips this column as a string; the decoder must parse it.
    assert set(get_unique_alerts("[1, 10]")) == {"GLAD-L", "RADD"}


def test_get_unique_alerts_invalid_string_returns_empty():
    assert get_unique_alerts("not a list") == []


def test_format_list_join_rules():
    assert format_list([]) == ""
    assert format_list(["GLAD-L"]) == "GLAD-L"
    assert format_list(["GLAD-L", "RADD"]) == "GLAD-L and RADD"
    assert format_list(["GLAD-L", "RADD", "CCDC"]) == "GLAD-L, RADD and CCDC"


def test_parse_formatted_string_is_inverse_of_format_list():
    for items in (["GLAD-L"], ["GLAD-L", "RADD"], ["GLAD-L", "RADD", "CCDC"]):
        assert parse_formatted_string(format_list(items)) == items
    assert parse_formatted_string("") == []


def test_ensure_list():
    assert ensure_list("x") == ["x"]
    assert ensure_list(["x", "y"]) == ["x", "y"]
    with pytest.raises(ValueError):
        ensure_list(123)


# ---------------------------------------------------------------------------
# The report is USER DATA: it must land in the user's SEPAL workspace, and the
# intermediates it needs must not touch a container path nobody creates.
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


def test_report_is_saved_into_the_user_workspace(ws, tmp_path, stub_plots):
    from deforestation_alerts.scripts.report_builder import (
        generate_deforestation_report_with_word_template,
    )
    from gui.tile.analysis.export import _DEFAULT_REPORT_TEMPLATE

    remote = ws.results_dir("recipe_a", "alert_0.docx")

    written = generate_deforestation_report_with_word_template(
        image_path=str(tmp_path / "unused.tif"),
        geodataframe_path=_alert_gpkg(tmp_path),
        template_path=_DEFAULT_REPORT_TEMPLATE,
        output_path=remote,
        workspace=ws,
    )

    assert written == remote
    # A real .docx (a zip container) landed in the user's workspace.
    assert ws.read_bytes(remote)[:2] == b"PK"
    # ...and nothing was left behind in the container's scratch.
    assert [p for p in (tmp_path / "scratch").rglob("*") if p.is_file()] == []


def test_report_intermediates_go_to_a_private_scratch_dir(ws, tmp_path, stub_plots):
    """The six JPEGs used to share one ``<module_dir>/temp``, so two concurrent
    reports overwrote each other's ``output_image1.jpg`` — and after the recipe
    folder went remote, nothing created that directory at all."""
    from deforestation_alerts.scripts.report_builder import (
        generate_deforestation_report_with_word_template,
    )
    from gui.tile.analysis.export import _DEFAULT_REPORT_TEMPLATE

    gpkg = _alert_gpkg(tmp_path)
    for name in ("alert_0.docx", "alert_1.docx"):
        generate_deforestation_report_with_word_template(
            image_path=str(tmp_path / "unused.tif"),
            geodataframe_path=gpkg,
            template_path=_DEFAULT_REPORT_TEMPLATE,
            output_path=ws.results_dir("recipe_a", name),
            workspace=ws,
        )

    assert len(stub_plots) == 12
    assert {p.name for p in stub_plots} == {
        f"output_image{i}.jpg" for i in range(1, 7)
    }
    # 12 renders, 12 distinct paths: the two runs never shared a directory.
    assert len(set(stub_plots)) == 12
    for path in stub_plots:
        assert path.is_relative_to(tmp_path / "scratch")
