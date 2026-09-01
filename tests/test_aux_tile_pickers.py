"""The Aux tile's two file pickers must browse the USER's SEPAL workspace.

A bare ``sw.inputs.FileInput`` lists the CONTAINER's filesystem, where the
user's attribution raster and custom report template do not exist — the picker
would simply show the wrong disk (inventory row a33). Downstream code
(``attribute_source``, the report builder) now assumes ``.v_model`` is a remote
path, so this is a load-bearing wiring, not cosmetics.
"""

from pysepal.sepalwidgets.file_input import FileInput

from deforestation_alerts.model.aux_model import AuxModel
from gui.tile.aux_tile import build_aux_layout


def _file_inputs(widget, found=None):
    found = [] if found is None else found
    if isinstance(widget, FileInput):
        found.append(widget)
    for child in getattr(widget, "children", None) or []:
        if hasattr(child, "children") or isinstance(child, FileInput):
            _file_inputs(child, found)
    return found


def test_both_pickers_are_backed_by_the_session_client(ws, monkeypatch):
    # The layout's four AssetSelects each spawn a GEE asset listing on
    # construction; this test is about the FILE pickers, so stub them out rather
    # than make an offline unit test depend on Earth Engine.
    import ipyvuetify as v
    from pysepal import sepalwidgets as sw

    monkeypatch.setattr(
        sw.inputs, "AssetSelect", lambda **kwargs: v.Select(items=[])
    )

    layout = build_aux_layout(AuxModel(), ws)

    pickers = _file_inputs(layout)
    assert len(pickers) == 2
    assert {tuple(p.extensions) for p in pickers} == {
        (".tif", ".tiff"), (".docx",),
    }
    for picker in pickers:
        assert picker.client is ws.client
        # Rooted at the SEPAL home, not the module results dir: these files can
        # live anywhere in the user's workspace.
        assert picker.root == ws.home
        assert picker.current_folder == ws.home


def test_band_names_stage_the_users_raster_down(ws, tmp_path):
    """``rasterio`` cannot open a remote path, so the picked raster is staged —
    and released again once its header has been read."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from gui.tile.aux_tile import _remote_band_names

    local = tmp_path / "r.tif"
    with rasterio.open(
        local, "w", driver="GTiff", height=2, width=2, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0.0, 10.0, 1.0, 1.0),
    ) as dst:
        dst.write(np.ones((2, 2), dtype="float32"), 1)
        dst.descriptions = ("forest",)
    remote = ws.results_dir("forest.tif")
    ws.write_bytes(remote, local.read_bytes())

    assert _remote_band_names(ws, remote) == ["forest"]
    assert [p for p in (tmp_path / "scratch").rglob("*") if p.is_file()] == []
