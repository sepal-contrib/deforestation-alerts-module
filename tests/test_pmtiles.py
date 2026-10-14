import json
import subprocess
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.engine import pmtiles
from gui.scripts import pmtiles_map


def _alerts():
    return gpd.GeoDataFrame(
        {"alert_id": [0, 1], "status": ["Not reviewed", "Confirmed"], "area_ha": [1.5, 0.5],
         "alert_date_min": [2026.01, None], "set_id": ["s", "s"]},
        geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01)], crs="EPSG:4326",
    )


def test_style_has_scalar_paints_and_numeric_filters():
    style = pmtiles_map.build_alert_style("http://x/a.pmtiles")
    layers = style["layers"]
    assert {layer["source-layer"] for layer in layers} == {"alerts", "points"}
    for layer in layers:
        assert layer["filter"][0] == "==" and layer["filter"][1] == "status_code"
        assert isinstance(layer["filter"][2], int)
        for v in layer["paint"].values():
            assert not isinstance(v, (list, dict)), "protomaps json_style has no expressions"
    assert len(layers) == 3 * len(pmtiles_map.STATUS_COLORS)  # fill + line + circle per status


def test_faded_style_uses_lighter_colours():
    normal = pmtiles_map.build_alert_style("u")["layers"][0]["paint"]["fill-color"]
    faded = pmtiles_map.build_alert_style("u", faded=True)["layers"][0]["paint"]["fill-color"]
    assert normal != faded


def test_resolve_tippecanoe_falls_back_next_to_python(monkeypatch, tmp_path):
    monkeypatch.setattr(pmtiles.shutil, "which", lambda name: None)
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir()
    (tmp_path / "bin" / "tippecanoe").write_text("")
    monkeypatch.setattr(pmtiles.sys, "executable", str(fake_python))
    assert pmtiles.resolve_tippecanoe() == str(tmp_path / "bin" / "tippecanoe")


def test_builds_two_layers_with_status_codes(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, check, capture_output, text):
        seen["cmd"] = cmd
        for arg in cmd:
            if arg.startswith("{"):
                spec = json.loads(arg)
                seen[spec["layer"]] = json.loads(Path(spec["file"]).read_text())
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"PMTiles")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pmtiles, "resolve_tippecanoe", lambda: "tippecanoe")
    monkeypatch.setattr(pmtiles.subprocess, "run", fake_run)
    out = pmtiles.alerts_to_pmtiles(_alerts(), tmp_path / "set" / "alerts.pmtiles")
    assert out.read_bytes() == b"PMTiles"
    polys, points = seen["alerts"]["features"], seen["points"]["features"]
    assert [f["properties"]["status_code"] for f in polys] == [0, 1]
    assert all(f["tippecanoe"]["minzoom"] == 11 for f in polys)
    assert points[0]["geometry"]["type"] == "Point"
    assert "-r1" in seen["cmd"]


def _fake_tippecanoe(monkeypatch):
    def fake_run(cmd, check, capture_output, text):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"PMTiles")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pmtiles, "resolve_tippecanoe", lambda: "tippecanoe")
    monkeypatch.setattr(pmtiles.subprocess, "run", fake_run)


def test_each_build_stages_under_its_own_part_name(monkeypatch, tmp_path):
    """Integrate and Overview can build one set's tiles at the same time: two
    builders sharing ``alerts.pmtiles.part`` would copy over each other's
    half-written file. Each build stages beside the target under a unique
    name and leaves nothing behind."""
    _fake_tippecanoe(monkeypatch)
    staged = []
    real_copy = pmtiles.shutil.copyfile
    monkeypatch.setattr(pmtiles.shutil, "copyfile", lambda src, dst: staged.append(Path(dst)) or real_copy(src, dst))
    out = tmp_path / "set" / "alerts.pmtiles"

    assert pmtiles.alerts_to_pmtiles(_alerts(), out) == out
    assert pmtiles.alerts_to_pmtiles(_alerts(), out) == out

    assert len(staged) == 2 and staged[0] != staged[1]
    for part in staged:
        assert part.parent == out.parent and part.name.startswith("alerts.pmtiles.") and part.suffix == ".part"
    assert sorted(f.name for f in out.parent.iterdir()) == ["alerts.pmtiles"]


def test_the_tiles_get_the_umasks_default_mode(monkeypatch, tmp_path):
    """The staged copy is renamed onto alerts.pmtiles, mode and all: staged
    with mkstemp it was 0600, unreadable by any other user or process that
    serves the tiles. It gets what a plainly created file gets."""
    import stat

    _fake_tippecanoe(monkeypatch)
    out = tmp_path / "set" / "alerts.pmtiles"
    pmtiles.alerts_to_pmtiles(_alerts(), out)
    reference = tmp_path / "set" / "reference"
    reference.write_bytes(b"")
    assert stat.S_IMODE(out.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


def test_a_guard_that_raises_keeps_the_built_tiles_off_disk(monkeypatch, tmp_path):
    """The guard runs right before the staged copy is moved onto the target:
    a caller that built from a store on disk refuses there when that store
    changed meanwhile, and the target is left as it was."""
    _fake_tippecanoe(monkeypatch)
    out = tmp_path / "alerts.pmtiles"
    out.write_bytes(b"previous")
    calls = []

    def guard():
        calls.append(sorted(f.name for f in tmp_path.iterdir()))
        raise RuntimeError("the store was rewritten")

    with pytest.raises(RuntimeError, match="rewritten"):
        pmtiles.alerts_to_pmtiles(_alerts(), out, guard=guard)
    [seen] = calls
    assert any(name.endswith(".part") for name in seen)  # the guard ran after the staging copy
    assert out.read_bytes() == b"previous" and sorted(f.name for f in tmp_path.iterdir()) == ["alerts.pmtiles"]

    pmtiles.alerts_to_pmtiles(_alerts(), out, guard=lambda: None)
    assert out.read_bytes() == b"PMTiles"


@pytest.mark.skipif(not pmtiles.tippecanoe_available(), reason="tippecanoe not installed")
def test_real_tippecanoe(tmp_path):
    out = pmtiles.alerts_to_pmtiles(_alerts(), tmp_path / "alerts.pmtiles")
    assert out.read_bytes()[:7] == b"PMTiles"
