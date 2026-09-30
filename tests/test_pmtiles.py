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
    assert {l["source-layer"] for l in layers} == {"alerts", "points"}
    for l in layers:
        assert l["filter"][0] == "==" and l["filter"][1] == "status_code"
        assert isinstance(l["filter"][2], int)
        for v in l["paint"].values():
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


@pytest.mark.skipif(not pmtiles.tippecanoe_available(), reason="tippecanoe not installed")
def test_real_tippecanoe(tmp_path):
    out = pmtiles.alerts_to_pmtiles(_alerts(), tmp_path / "alerts.pmtiles")
    assert out.read_bytes()[:7] == b"PMTiles"
