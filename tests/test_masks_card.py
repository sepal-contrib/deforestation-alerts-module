"""The Masks card and ``MaskModal`` of Download & harmonize (spec §7.3, §5.1).

The card's pure row builder and switch are tested directly; the card itself
is rendered with recorders standing in for ``ProductTable``, ``MaskModal``
and ``ConfirmDialog`` (the pattern of ``tests/test_alerts_step.py``), and the
modal is rendered for real with a stub raster input (the pattern of
``tests/test_source_modal.py``).
"""

import asyncio
import time
import threading
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import numpy as np
import pytest
import rasterio
import reacton
import reacton.ipyvuetify as rv
import solara
from affine import Affine
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine.fingerprint import Status, mask_key, mask_raw_key
from deforestation_alerts.engine.plan import MaskRow
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, Artifact, NativeGrid, Period, PeriodMask, Project, add_mask,
    set_period_mask,
)
from gui.i18n import t, tn
from gui.scripts.map_layers import MapLayers
from gui.store.project_writers import writing
from gui.store.state_manager import AppState
from gui.tile import masks_card
from gui.widget import mask_modal
from gui.widget.enter_key import ENTER_DEFAULT

FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))
MAR = Period(id="2026-03-01_2026-03-31", start=date(2026, 3, 1), end=date(2026, 3, 31))


def _p():
    p = Project(name="mc", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    p.periods = {FEB.id: FEB}
    p.active_period = FEB.id
    add_mask(p, name="forest", origin="gee", ref="UMD/x", asset_type="IMAGE", band="b",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="keep", period_id=FEB.id)
    return p


def _with_tif(p):
    """``_p()`` plus a GeoTIFF mask ``tif`` (excluded in February)."""
    add_mask(p, name="tif", origin="file", ref="/x/tif.tif", asset_type="IMAGE", band=1,
             native=NativeGrid(crs="EPSG:32721", scale=30.0), mode="exclude", period_id=FEB.id)
    return p


def _rows(rows, p=None, **over):
    kw = dict(running=set(), errors={}, on=set(), building=set(), run=lambda n: None, rerun=lambda n: None,
              toggle=lambda n: None, info=lambda n: None, edit=lambda n: None, delete=lambda n: None,
              switch=lambda n, **k: None)
    kw.update(over)
    return {r["key"]: r for r in masks_card.mask_table_rows(p or _with_tif(_p()), "/tmp/x", FEB, rows, **kw)}


def _menu(row) -> dict:
    """The row's ⋮ menu items, by label."""
    return {i["label"]: i for i in next(a for a in row["actions"] if a["kind"] == "menu")["items"]}


def _kind(row, kind) -> dict:
    return next(a for a in row["actions"] if a["kind"] == kind)


def _art(kind, rel_path, size_bytes=0):
    key = mask_raw_key("forest") if kind == "raw_mask" else mask_key("forest")
    return Artifact(key=key, kind=kind, rel_path=rel_path, fingerprint="f", inputs={}, content_id="c",
                    size_bytes=size_bytes, created_at=datetime.now())


def _toasts(bus, kind, message):
    """How many times ``message`` was toasted: pysepal merges identical
    non-error toasts within 2 s into one, bumping its ``count`` (ruling C2)."""
    return sum(x.count for x in bus.toasts.value if x.type.value == kind and x.message == message)


# ---------------------------------------------------------------- the rows
GEE_MISSING = MaskRow("forest", "gee", Status.MISSING, Status.MISSING, True, "keep")
EE, TIF = t("steps.raster_input.source_gee"), t("steps.raster_input.source_file")
DOWNLOAD_AGAIN, EDIT, DELETE = (t("steps.prepare.action_download_again"), t("widgets.product_table.action_edit"),
                                t("steps.prepare.mask_delete_action"))


def test_a_keep_mask_covering_none_of_the_aoi_warns_on_its_row():
    out = _rows([MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep", stats={"on_pixels": 0})])
    assert out["forest"]["notes"] == [{"text": "covers none of the AOI: every alert would be removed",
                                       "tone": "warning"}]
    # an exclude mask covering nothing removes nothing: no warning, and no neutral line
    out = _rows([MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "exclude",
                         stats={"on_pixels": 0})])
    assert out["forest"]["notes"] == []


def test_mask_actions_are_map_info_menu():
    out = _rows([GEE_MISSING])["forest"]
    kinds = [a["kind"] for a in out["actions"]]
    assert kinds == ["map_toggle", "info", "menu"]
    assert len(kinds) == masks_card.MASK_ACTIONS  # the budget's count
    assert list(_menu(out)) == [DOWNLOAD_AGAIN, EDIT, DELETE]
    assert _menu(out)[EDIT]["icon"] == "mdi-pencil-outline"
    assert _menu(out)[DELETE]["icon"] == "mdi-delete-outline" and _menu(out)[DELETE]["color"] == "error"
    assert not any(i.get("disabled") for i in _menu(out).values())
    assert not _kind(out, "map_toggle")["disabled"]  # an Earth Engine mask is shown before alignment
    tif = _rows([MaskRow("tif", "file", None, Status.MISSING, True, "exclude")])["tif"]
    assert list(_menu(tif)) == [EDIT, DELETE]  # nothing to fetch


def test_the_mask_columns_leave_the_name_its_budget():
    cols = masks_card.mask_columns()
    assert [c["label"] for c in cols] == [t("steps.prepare.col_mask"), t("steps.prepare.col_mode"),
                                          t("steps.prepare.col_status")]
    assert [c.get("width") for c in cols] == [None, "78px", "104px"]


def test_an_earth_engine_mask_row_reads_its_name_subtitle_and_download_pill():
    out = _rows([GEE_MISSING])["forest"]
    assert out["cells"][0] == {"type": "title", "value": "forest", "subtitle": t("steps.prepare.why_not_downloaded")}
    assert out["cells"][1]["type"] == "render"  # the Mode select
    state = out["cells"][2]
    assert (state["type"], state["label"], state["icon"], state["color"]) == (
        "verb", t("steps.prepare.verb_download"), "mdi-play", "primary")


def test_a_geotiff_mask_has_no_download_status_nor_redownload():
    out = _rows([MaskRow("tif", "file", None, Status.MISSING, True, "exclude")])["tif"]
    assert out["cells"][2]["type"] == "verb" and out["cells"][2]["label"] == t("steps.prepare.verb_align")
    assert out["cells"][0]["subtitle"] == t("steps.prepare.why_not_aligned")
    assert DOWNLOAD_AGAIN not in _menu(out) and _kind(out, "map_toggle")["disabled"]
    # once aligned, its toggle is enabled
    out = _rows([MaskRow("tif", "file", None, Status.CURRENT, True, "exclude")])
    assert not _kind(out["tif"], "map_toggle")["disabled"]


@pytest.mark.parametrize("raw, aligned, verb, stale", [
    (Status.MISSING, Status.MISSING, "download", False),  # the download, when it is not current
    (Status.STALE, Status.CURRENT, "download", True),
    (Status.MISSING, Status.STALE, "download", False),
    (Status.CURRENT, Status.MISSING, "align", False),  # else the alignment
    (Status.CURRENT, Status.STALE, "align", True),
    (None, Status.MISSING, "align", False),  # a GeoTIFF: the alignment only
    (None, Status.STALE, "align", True),
])
def test_the_pill_runs_the_first_stage_that_is_not_current(raw, aligned, verb, stale):
    origin, name = ("gee", "forest") if raw is not None else ("file", "tif")
    cell = _rows([MaskRow(name, origin, raw, aligned, True, "keep")])[name]["cells"][2]
    assert cell["type"] == "verb" and cell["label"] == t(f"steps.prepare.verb_{verb}")
    assert (cell["icon"], cell["color"]) == (("mdi-update", "warning") if stale else ("mdi-play", "primary"))


def test_a_ready_mask_reads_ready_and_its_origin_and_size():
    p = _with_tif(_p())
    p.artifacts = {
        mask_raw_key("forest"): _art("raw_mask", "masks/raw/mask_forest.tif", 2_000_000),
        mask_key("forest"): _art("mask", "masks/mask_forest.tif", 3_000_000),
    }
    out = _rows([MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep")], p=p)["forest"]
    assert out["cells"][0]["subtitle"] == f"{EE} · 5.0 MB"  # the download and the aligned file
    state = out["cells"][2]
    assert (state["type"], state["label"], state["color"]) == ("state", t("steps.prepare.state_ready"), "success")
    tif = _rows([MaskRow("tif", "file", None, Status.CURRENT, True, "keep")])["tif"]
    assert tif["cells"][0]["subtitle"] == TIF  # no file recorded: no size


def test_an_off_mask_keeps_its_align_pill():
    out = _rows([MaskRow("tif", "file", None, Status.STALE, False, "exclude")])["tif"]
    state = out["cells"][2]
    # neutral: an off mask holds no period back, but can still be aligned and previewed
    assert (state["type"], state["label"], state["icon"], state["color"]) == (
        "verb", t("steps.prepare.verb_align"), "mdi-play", "primary")
    assert out["cells"][0]["subtitle"] == f"{t('steps.prepare.why_not_aligned')} · {t('steps.prepare.state_off')}"
    ready = _rows([MaskRow("tif", "file", None, Status.CURRENT, False, "exclude")])["tif"]
    assert ready["cells"][0]["subtitle"] == f"{TIF} · {t('steps.prepare.state_off')}"


def test_a_stale_mask_says_why_and_what_the_run_redoes():
    p = _with_tif(_p())
    p.artifacts = {mask_key("tif"): Artifact(key=mask_key("tif"), kind="mask", rel_path="masks/mask_tif.tif",
                                             fingerprint="f", inputs={"grid": "EPSG:32721|10|0|0|1x1"},
                                             content_id="c", size_bytes=1, created_at=datetime.now())}
    from deforestation_alerts.engine.why import mask_reason
    from gui.scripts.prepare_view import reason_text

    reason = mask_reason(p, "tif")
    out = _rows([MaskRow("tif", "file", None, Status.STALE, True, "exclude")], p=p)["tif"]
    assert out["cells"][0]["subtitle"] == t("steps.prepare.sub_again_align", why=reason_text(reason))


def test_a_mask_whichever_periods_job_claimed_it_reads_queued_then_its_stage():
    from gui.scripts.prepare_jobs import progress_key
    from gui.tile.prepare_step import mask_inflight_key

    running = {mask_inflight_key("mc", "forest")}
    out = _rows([GEE_MISSING], running=running)["forest"]
    assert out["cells"][2] == {"type": "state", "icon": "mdi-timer-sand", "color": "grey",
                               "label": t("steps.prepare.state_queued")}
    assert out["cells"][0]["subtitle"] == t("steps.prepare.sub_queued")
    assert _menu(out)[DOWNLOAD_AGAIN]["disabled"]
    key = progress_key("mc", FEB.id, "mask", "forest")  # project-wide
    out = _rows([GEE_MISSING], running=running, stages={key: "download"}, progress={key: 12})["forest"]
    assert out["cells"][2]["label"] == t("steps.prepare.state_downloading_pct", pct=12)
    assert out["cells"][2]["progress"] == 12
    assert out["cells"][0]["subtitle"] == t("steps.prepare.sub_downloading_align", pct=12)
    # a stage left behind by a row no longer claimed is not shown
    assert _rows([GEE_MISSING], stages={key: "download"})["forest"]["cells"][2]["type"] == "verb"


def test_running_mask_shows_aligning():
    from gui.scripts.prepare_jobs import progress_key
    from gui.tile.prepare_step import mask_inflight_key

    key = progress_key("mc", FEB.id, "mask", "tif")
    out = _rows([MaskRow("tif", "file", None, Status.MISSING, True, "keep")],
                running={mask_inflight_key("mc", "tif")}, stages={key: "align"})["tif"]
    assert out["cells"][2]["label"] == t("steps.prepare.state_aligning")
    assert out["cells"][0]["subtitle"] == t("steps.prepare.sub_aligning")


def test_a_locked_mask_shows_text_not_verbs():
    """A review copy (spec §6.3): the status as text with no pill, every run,
    refresh, edit and delete greyed; Details still opens."""
    for row, label in ((GEE_MISSING, "state_to_download"),
                       (MaskRow("tif", "file", None, Status.STALE, True, "keep"), "state_to_align")):
        out = _rows([row], locked=True)[row.name]
        state = out["cells"][2]
        assert state["type"] == "state" and state["label"] == t(f"steps.prepare.{label}")
        assert "on_click" not in state
        assert all(i["disabled"] for i in _menu(out).values())
        assert not _kind(out, "info").get("disabled")
    # a locked Earth Engine mask shows only its local raster (spec §6.3)
    assert _kind(_rows([GEE_MISSING], locked=True)["forest"], "map_toggle")["disabled"]


def test_a_failed_mask_offers_retry():
    out = _rows([GEE_MISSING], errors={"@mask:forest": "boom"})["forest"]
    assert out["cells"][2]["label"] == t("steps.prepare.verb_retry") and out["cells"][2]["color"] == "error"


def test_a_rows_actions_call_back_with_its_name_and_its_error_is_keyed():
    calls = []
    out = _rows([GEE_MISSING],
                errors={"@mask:forest": "boom", "forest": "a source's"},
                run=lambda n: calls.append(("run", n)), rerun=lambda n: calls.append(("rerun", n)),
                toggle=lambda n: calls.append(("toggle", n)), info=lambda n: calls.append(("info", n)),
                edit=lambda n: calls.append(("edit", n)), delete=lambda n: calls.append(("delete", n)),
                info_open="forest")["forest"]
    assert out["error"] == "boom"
    assert _kind(out, "info")["is_on"]
    out["cells"][2]["on_click"]()
    _kind(out, "map_toggle")["on_click"]()
    _kind(out, "info")["on_click"]()
    for item in _menu(out).values():
        item["on_click"]()
    assert calls == [("run", "forest"), ("toggle", "forest"), ("info", "forest"), ("rerun", "forest"),
                     ("edit", "forest"), ("delete", "forest")]


def test_a_geotiff_mask_whose_file_is_gone_says_so_and_cannot_run():
    out = _rows([MaskRow("tif", "file", None, Status.MISSING, True, "keep", file_missing="/x/tif.tif")])["tif"]
    assert out["notes"] == [{"text": t("steps.prepare.file_missing", path="/x/tif.tif"), "tone": "error"}]
    assert out["cells"][2]["type"] == "state" and out["cells"][2]["label"] == t("steps.prepare.state_file_missing")
    assert out["cells"][0]["subtitle_tone"] == "error"
    assert out["cells"][0]["subtitle"] == t("steps.prepare.sub_file_missing")  # not "File missing" twice


def test_the_mode_select_reads_off_for_a_mask_that_is_off():
    out = _rows([MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, False, "exclude")])["forest"]

    @reacton.component
    def Cell():
        with rv.Html(tag="div") as main:
            out["cells"][1]["fn"]()
        return main

    _box, rc = reacton.render(Cell(), handle_error=False)
    try:
        select = rc.find(v.Select).widget
        assert select.v_model == "off"
        assert [i["value"] for i in select.items] == ["off", "keep", "exclude"]
    finally:
        rc.close()


def test_a_switch_only_edits_the_project():
    state = AppState()
    state.load_project_state(_p())
    before = state.project.value.periods[FEB.id]
    assert masks_card.switch_mask(state, "mc", FEB.id, "forest", mode="exclude")
    assert state.project.value.periods[FEB.id].masks["forest"] == PeriodMask(on=True, mode="exclude")
    assert before.masks["forest"].mode == "keep"  # the published snapshot was not mutated
    assert masks_card.switch_mask(state, "mc", FEB.id, "forest", on=False)
    assert state.project.value.periods[FEB.id].masks["forest"] == PeriodMask(on=False, mode="exclude")
    assert not masks_card.switch_mask(state, "other", FEB.id, "forest", on=True)  # not the open project


# ---------------------------------------------------------------- validation
def _tif(path, count=2, dtype="float32", crs="EPSG:32721"):
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=count, dtype=dtype, crs=crs,
                       transform=Affine(30, 0, 500000, 0, -30, 8900000)) as dst:
        dst.write(np.zeros((count, 4, 4), dtype))
    return path


def test_validate_a_geotiff_mask(tmp_path):
    path = _tif(tmp_path / "forest.tif")
    out = asyncio.run(mask_modal.validate_mask(None, _p(), name="probability", origin="file", ref=str(path),
                                               asset_type="FILE", band=2))
    assert out["fields"]["band"] == 2 and out["fields"]["native"].scale == 30.0
    bad = asyncio.run(mask_modal.validate_mask(None, _p(), name="x", origin="file", ref=str(path),
                                               asset_type="FILE", band=3))
    assert bad == {"error": "steps.alerts.err_missing_band"}
    assert asyncio.run(mask_modal.validate_mask(None, _p(), name="Forest", origin="file", ref=str(path),
                                                asset_type="FILE", band=1)) == {
        "error": "steps.prepare.mask_name_duplicate"}


def test_a_geotiff_mask_needs_a_band_index_a_crs_and_a_readable_file(tmp_path):
    p = _p()
    path = _tif(tmp_path / "f.tif")
    run = lambda **kw: asyncio.run(mask_modal.validate_mask(None, p, **{  # noqa: E731
        "name": "m", "origin": "file", "ref": str(path), "asset_type": "FILE", "band": 1, **kw}))
    assert run(band="treecover") == {"error": "steps.prepare.mask_err_incomplete"}  # a name left from an asset
    assert run(band=None) == {"error": "steps.prepare.mask_err_incomplete"}
    assert run(ref="") == {"error": "steps.prepare.mask_err_incomplete"}
    assert run(ref=str(tmp_path / "gone.tif"))["error"].startswith("steps.")
    assert run(ref=str(_tif(tmp_path / "nocrs.tif", crs=None))) == {"error": "steps.alerts.err_no_crs"}
    assert run(band="1")["fields"]["band"] == 1  # an index read as text


def test_an_edit_keeps_its_own_name(tmp_path):
    path = _tif(tmp_path / "forest.tif")
    out = asyncio.run(mask_modal.validate_mask(None, _p(), name="forest", origin="file", ref=str(path),
                                               asset_type="FILE", band=1, editing="forest"))
    assert out["fields"]["name"] == "forest"


def test_validate_an_earth_engine_mask(monkeypatch):
    monkeypatch.setattr(mask_modal, "discovery_request", lambda ref, kind, bands: ("DISC", tuple(bands)))
    asked = []

    async def get_info_async(obj):
        asked.append(obj)
        return {"count": 1, "crs": "EPSG:4326", "scale": 27.83, "crss": ["EPSG:4326"], "min_scale": 27.83,
                "projection": {"transform": [0.00025, 0, -180, 0, -0.00025, 80]},
                "band_types": {"treecover2000": {"precision": "int"}}}

    gee = SimpleNamespace(get_info_async=get_info_async)
    out = asyncio.run(mask_modal.validate_mask(gee, _p(), name="hansen", origin="gee", ref="UMD/hansen",
                                               asset_type="IMAGE", band="treecover2000"))
    assert out["fields"]["native"].scale == 27.83 and out["fields"]["band"] == "treecover2000"
    assert asked == [("DISC", ("treecover2000",))]
    # a band the asset lacks, and a table, are refused
    out = asyncio.run(mask_modal.validate_mask(gee, _p(), name="hansen", origin="gee", ref="UMD/hansen",
                                               asset_type="IMAGE", band="loss"))
    assert out == {"error": "steps.alerts.err_missing_band"}
    out = asyncio.run(mask_modal.validate_mask(gee, _p(), name="hansen", origin="gee", ref="UMD/t",
                                               asset_type="TABLE", band="loss"))
    assert out == {"error": "steps.alerts.err_wrong_type"} and len(asked) == 2


def test_a_collection_mixing_crss_downloads_in_wgs84_at_its_finest_scale(monkeypatch):
    monkeypatch.setattr(mask_modal, "discovery_request", lambda ref, kind, bands: "DISC")

    async def get_info_async(obj):
        return {"count": 3, "crs": "EPSG:32721", "scale": 10.0, "crss": ["EPSG:32721", "EPSG:32722"],
                "min_scale": 10.0, "band_types": {"b": {"precision": "int"}}}

    out = asyncio.run(mask_modal.validate_mask(SimpleNamespace(get_info_async=get_info_async), _p(), name="s2",
                                               origin="gee", ref="X/col", asset_type="IMAGE_COLLECTION", band="b"))
    assert out["fields"]["native"].crs == "EPSG:4326" and out["fields"]["native"].scale == 10.0


# ---------------------------------------------------------------- the card, rendered
class _Map:
    def __init__(self):
        self.layers, self.ee = {}, []

    def add_layer(self, layer, key=""):
        self.layers[key] = layer

    def remove_layer(self, key, none_ok=False):
        self.layers.pop(key, None)

    def add_ee_layer(self, image, vis, name="", key="", opacity=1.0):
        self.ee.append((image, vis, name, key, opacity))
        self.layers[key] = image


def _sync_spawn(monkeypatch):
    """Run MapLayers builds on the calling thread."""
    import gui.scripts.map_layers as ml

    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: target(*args))


def _card(monkeypatch, state, *, rows=None, running=frozenset(), layers=None, map_=None, dialogs=None,
          on_rerun=lambda n: None, stages=None):
    """Render the card with recorders: ``confirm`` is the delete confirm's
    props; ``dialogs`` (when given) gets ``redownload`` (the Download again
    confirm's) and ``details`` (the Details dialog's)."""
    seen, modal, confirm = {}, {}, {}
    dialogs = {} if dialogs is None else dialogs
    monkeypatch.setattr(masks_card, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(masks_card, "MaskModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(masks_card, "ConfirmDialog", lambda **kw: (
        confirm if kw["confirm_label"] == t("common.delete") else dialogs.setdefault("redownload", {})).update(kw))
    monkeypatch.setattr(masks_card, "DetailsDialog", lambda **kw: dialogs.setdefault("details", {}).update(kw))
    map_ = map_ or _Map()
    layers = layers or MapLayers(map_)
    rows = rows if rows is not None else [MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep")]

    period = state.project.value.periods[state.project.value.active_period]

    @reacton.component
    def Page():
        NotificationProvider()
        masks_card.MasksCard(app_state=state, map_=map_, gee_interface=None, layers=layers, period=period, rows=rows,
                             running=running, errors={}, on_run=lambda n: None, on_rerun=on_rerun, stages=stages)

    box, rc = reacton.render(Page(), handle_error=False)
    return rc, seen, modal, confirm


def _action(seen, name, kind):
    """A row action's callback; ``edit`` and ``delete`` are its ⋮ menu's."""
    row = next(r for r in seen["rows"] if r["key"] == name)
    if kind in ("edit", "delete"):
        return _menu(row)[EDIT if kind == "edit" else DELETE]["on_click"]
    return _kind(row, kind)["on_click"]


def _state(p):
    state = AppState()
    state.load_project_state(p)
    return state


def _used_by_a_set(p):
    p.alert_sets = {"s": AlertSet(id="s", period_id=FEB.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                  params=AlertSetParams(), store_rel_path="s", combined_rel_path="c",
                                  footprint_rel_path="f", fingerprint="f", created_at=datetime.now(),
                                  masks_used=[{"name": "forest", "mode": "keep", "content_id": "c"}])}


def test_edit_and_its_save_wait_for_the_projects_writers(monkeypatch):
    """Spec §5.1: editing a mask is refused while a job writes into the
    project, when the modal would open and at Save (Task 34's pattern)."""
    state = _state(_p())

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state)
        try:
            bus = get_current_bus()
            with writing("mc"):
                _action(seen, "forest", "edit")()
                assert modal["open_"].value is False  # not opened
                # the modal shows the refusal inline: a translated message, never a key (ruling)
                with pytest.raises(ValueError, match=t("steps.prepare.mask_edit_busy")):
                    modal["on_save"]("forest", {"ref": "UMD/y"})  # a job started while it was open
            assert state.project.value.masks["forest"].ref == "UMD/x"
            assert _toasts(bus, "warning", t("steps.prepare.mask_edit_busy")) == 2
        finally:
            rc.close()

    asyncio.run(main())


def test_a_save_is_refused_once_the_masks_own_job_claimed_it(monkeypatch):
    """A mask Run claims its key before its worker marks the project as
    being written: a Save in between is refused too."""
    from gui.tile.prepare_step import mask_inflight_key

    state = _state(_p())

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state, running=frozenset({mask_inflight_key("mc", "forest")}))
        try:
            with pytest.raises(ValueError, match=t("steps.prepare.mask_edit_busy")):
                modal["on_save"]("forest", {"ref": "UMD/y"})
            assert state.project.value.masks["forest"].ref == "UMD/x"
        finally:
            rc.close()

    asyncio.run(main())


def test_edit_is_refused_once_a_set_used_the_mask_and_a_save_once_one_did_meanwhile(monkeypatch):
    state = _state(_p())

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state)
        try:
            bus = get_current_bus()
            _action(seen, "forest", "edit")()
            assert modal["editing"] == "forest" and modal["open_"].value is True
            state.update_project("mc", _used_by_a_set)  # an Integrate ran while the modal was open
            with pytest.raises(ValueError, match=t("steps.prepare.mask_edit_used")):
                modal["on_save"]("forest", {"ref": "UMD/y"})
            assert state.project.value.masks["forest"].ref == "UMD/x"
            modal["open_"].set(False)
            _action(seen, "forest", "edit")()
            assert modal["open_"].value is False
            assert _toasts(bus, "warning", t("steps.prepare.mask_edit_used")) >= 2
        finally:
            rc.close()

    asyncio.run(main())


def test_a_save_applies_the_edit_and_drops_the_old_preview(monkeypatch):
    _sync_spawn(monkeypatch)
    state = _state(_p())
    map_ = _Map()
    layers = MapLayers(map_)

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state, layers=layers, map_=map_)
        try:
            _action(seen, "forest", "edit")()
            layers.show("mask:forest", lambda: map_.add_layer("x", key="mask:forest"))
            modal["on_save"]("forest", {"ref": "UMD/y", "band": "c"})
            m = state.project.value.masks["forest"]
            assert (m.ref, m.band) == ("UMD/y", "c")
            assert "mask:forest" not in layers.on.value and "mask:forest" not in map_.layers
        finally:
            rc.close()

    asyncio.run(main())


def test_add_turns_the_mask_on_in_the_active_period_only_and_raises_translated_errors(monkeypatch):
    p = _p()
    p.periods = {**p.periods, MAR.id: MAR}
    state = _state(p)
    fields = dict(name="water", origin="gee", ref="JRC/w", asset_type="IMAGE", band="occurrence",
                  native=NativeGrid(crs="EPSG:4326", scale=30.0))

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state)
        try:
            modal["on_add"](fields, "exclude")
            live = state.project.value
            assert live.masks["water"].ref == "JRC/w"
            assert live.periods[FEB.id].masks["water"] == PeriodMask(on=True, mode="exclude")
            assert "water" not in live.periods[MAR.id].masks
            with pytest.raises(ValueError, match=t("steps.prepare.mask_name_duplicate")):
                modal["on_add"]({**fields, "name": "Water"}, "keep")
        finally:
            rc.close()

    asyncio.run(main())


def test_a_switch_from_the_row_edits_the_period_and_says_a_run_reharmonizes(monkeypatch):
    state = _state(_p())

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state)
        try:
            bus = get_current_bus()
            row = next(r for r in seen["rows"] if r["key"] == "forest")
            assert row["cells"][1]["type"] == "render"

            @reacton.component
            def Cells():  # the Mode cell, as ProductTable renders it
                with rv.Html(tag="div") as main:
                    row["cells"][1]["fn"]()
                return main

            box, cells = reacton.render(Cells(), handle_error=False)
            try:
                select = cells.find(v.Select).widget
                assert select.v_model == "keep"
                select.v_model = "exclude"
                assert state.project.value.periods[FEB.id].masks["forest"] == PeriodMask(on=True, mode="exclude")
                select.v_model = "off"  # off keeps the mode for when it is on again
                assert state.project.value.periods[FEB.id].masks["forest"] == PeriodMask(on=False, mode="exclude")
            finally:
                cells.close()
            assert _toasts(bus, "info", t("steps.prepare.masks_switched")) == 2
        finally:
            rc.close()

    asyncio.run(main())


def test_delete_names_the_sets_and_removes_the_mask_its_files_and_every_periods_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    p = _p()
    p.periods = {**p.periods, MAR.id: MAR}
    set_period_mask(p, MAR.id, "forest", on=True, mode="exclude")
    pdir = paths.project_dir("mc")
    (pdir / "masks" / "raw").mkdir(parents=True)
    for rel in ("masks/raw/mask_forest.tif", "masks/mask_forest.tif"):
        (pdir / rel).write_bytes(b"x")
    p.artifacts = {
        mask_raw_key("forest"): _art("raw_mask", "masks/raw/mask_forest.tif"),
        mask_key("forest"): _art("mask", "masks/mask_forest.tif"),
    }
    _used_by_a_set(p)
    state = _state(p)
    map_ = _Map()
    layers = MapLayers(map_)

    async def main():
        rc, seen, modal, confirm = _card(monkeypatch, state, layers=layers, map_=map_)
        try:
            layers.show("mask:forest", lambda: map_.add_layer("x", key="mask:forest"))
            _action(seen, "forest", "delete")()
            assert confirm["open"] is True and confirm["message"] == tn("steps.prepare.mask_delete_used", 1)
            confirm["on_confirm"]()
            live = state.project.value
            assert "forest" not in live.masks and live.artifacts == {}
            assert all("forest" not in per.masks for per in live.periods.values())
            assert not (pdir / "masks/raw/mask_forest.tif").exists() and not (pdir / "masks/mask_forest.tif").exists()
            assert "mask:forest" not in map_.layers
            assert live.alert_sets["s"].masks_used[0]["name"] == "forest"  # the set keeps what it applied
        finally:
            rc.close()

    asyncio.run(main())


def test_delete_is_refused_while_a_writer_runs(monkeypatch):
    state = _state(_p())

    async def main():
        rc, seen, modal, confirm = _card(monkeypatch, state)
        try:
            bus = get_current_bus()
            _action(seen, "forest", "delete")()
            assert confirm["message"] == t("steps.prepare.mask_delete_message")
            with writing("mc"):
                confirm["on_confirm"]()
            assert "forest" in state.project.value.masks
            assert _toasts(bus, "warning", t("steps.prepare.mask_delete_busy")) == 1
        finally:
            rc.close()

    asyncio.run(main())


def test_show_on_map_draws_an_unaligned_earth_engine_mask_as_earth_engine_tiles(monkeypatch):
    from gui.scripts import raster_map

    _sync_spawn(monkeypatch)
    monkeypatch.setattr(masks_card.aoi_io, "ee_aoi", lambda result, pdir: "AOI")

    class _Img:
        def __init__(self, what):
            self.what = what

        def selfMask(self):
            return _Img(("selfMask", self.what))

    monkeypatch.setattr(masks_card, "build_mask_image", lambda mask, aoi: _Img((mask.name, aoi)))
    state = _state(_p())
    map_ = _Map()
    layers = MapLayers(map_)

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state, map_=map_, layers=layers,
                                   rows=[MaskRow("forest", "gee", Status.MISSING, Status.MISSING, True, "keep")])
        try:
            _action(seen, "forest", "map_toggle")()
            [(img, vis, name, key, opacity)] = map_.ee
            assert img.what == ("selfMask", ("forest", "AOI"))
            assert (vis, name, key, opacity) == (raster_map.MASK_EE_VIS, "Mask forest", "mask:forest",
                                                 raster_map.MASK_OPACITY)
            (legend,) = layers.legends.value  # spec §9.4: the active period's choice
            assert legend.layer_id == "mask:forest" and legend.spec.labels[0].key == "legend.mask.keep"
            assert layers.content_of("mask:forest") is None  # Earth Engine tiles: no file yet
            _action(seen, "forest", "map_toggle")()  # off again
            assert "mask:forest" not in map_.layers
        finally:
            rc.close()

    asyncio.run(main())


def test_show_on_map_draws_the_aligned_file_in_the_mask_grey(monkeypatch, tmp_path):
    from gui.scripts import raster_map

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    drawn = []
    monkeypatch.setattr(masks_card.raster_map, "add_raster_layer",
                        lambda map_, path, **kw: drawn.append((str(path), kw)) or map_.add_layer("r", key=kw["key"]))
    p = _p()
    p.artifacts = {mask_key("forest"): _art("mask", "masks/mask_forest.tif")}
    set_period_mask(p, FEB.id, "forest", mode="exclude")
    state = _state(p)
    map_ = _Map()
    layers = MapLayers(map_)

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state, map_=map_, layers=layers)
        try:
            _action(seen, "forest", "map_toggle")()
            [(path, kw)] = drawn
            assert path == str(paths.project_dir("mc") / "masks/mask_forest.tif")
            assert kw["colormap"] == raster_map.MASK_COLORMAP and kw["opacity"] == raster_map.MASK_OPACITY
            assert kw["key"] == "mask:forest" and kw["nodata"] == 0 and kw["name"] == "Mask forest"
            (legend,) = layers.legends.value  # spec §9.4: the active period's choice
            assert legend.label.key == "legend.name.mask" and legend.spec.labels[0].key == "legend.mask.exclude"
            # the aligned file's content id is recorded: a re-run's refresh redraws
            # a new file, and only a new one (spec §9.1)
            assert layers.content_of("mask:forest") == "c"
            layers.refresh("mask:forest", lambda: drawn.append("same"), "c")
            layers.refresh("mask:forest", lambda: drawn.append("new"), "c2")
            assert drawn[1:] == ["new"]
        finally:
            rc.close()

    asyncio.run(main())


def test_the_card_lists_the_rows_with_the_note_and_opens_add(monkeypatch):
    state = _state(_p())

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state)
        try:
            assert seen["title"] == t("steps.prepare.masks_title")
            assert seen["columns"] == masks_card.mask_columns() and len(seen["columns"]) == 3
            assert rc.find(v.Html).widgets  # rendered
            texts = [w for w in rc.find(v.Html).widgets if t("steps.prepare.masks_note") in str(w.children)]
            assert texts
            [add] = [b for b in rc.find(v.Btn).widgets if t("steps.prepare.mask_add") in b.children]
            add.fire_event("click", None)
            assert modal["open_"].value is True and modal["editing"] is None
        finally:
            rc.close()

    asyncio.run(main())


def test_details_open_from_the_info_action_and_close_with_their_row(monkeypatch):
    state = _state(_p())
    dialogs = {}

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state, dialogs=dialogs, rows=[GEE_MISSING])
        try:
            assert dialogs["details"]["open"] is False
            _action(seen, "forest", "info")()
            details = dialogs["details"]
            assert details["open"] is True and details["details"]["title"] == "forest"
            assert details["details"]["lead"] == t("steps.prepare.state_to_download")
            assert details["details"]["why"] == t("steps.prepare.why_not_downloaded")
            assert _kind(seen["rows"][0], "info")["is_on"]
            details["on_close"]()
            assert dialogs["details"]["open"] is False
            _action(seen, "forest", "info")()
            _action(seen, "forest", "info")()  # a second click closes it
            assert dialogs["details"]["open"] is False
        finally:
            rc.close()

    asyncio.run(main())


def test_download_again_asks_first_then_reruns(monkeypatch):
    state = _state(_p())
    dialogs, reruns = {}, []

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state, dialogs=dialogs, on_rerun=reruns.append)
        try:
            row = seen["rows"][0]
            _menu(row)[DOWNLOAD_AGAIN]["on_click"]()
            redo = dialogs["redownload"]
            assert redo["open"] is True and reruns == []
            assert redo["title"] == t("steps.prepare.mask_redownload_title", name="forest")
            assert redo["message"] == t("steps.prepare.mask_redownload_message")
            assert redo["confirm_label"] == t("steps.prepare.redownload_label") and redo["confirm_color"] == "primary"
            redo["on_cancel"]()
            assert dialogs["redownload"]["open"] is False and reruns == []
            _menu(seen["rows"][0])[DOWNLOAD_AGAIN]["on_click"]()
            dialogs["redownload"]["on_confirm"]()
            assert reruns == ["forest"] and dialogs["redownload"]["open"] is False
        finally:
            rc.close()

    asyncio.run(main())


def test_the_card_passes_the_row_stages(monkeypatch):
    from gui.scripts.prepare_jobs import progress_key
    from gui.tile.prepare_step import mask_inflight_key

    state = _state(_p())
    key = progress_key("mc", FEB.id, "mask", "forest")
    dialogs = {}

    async def main():
        rc, seen, *_ = _card(monkeypatch, state, dialogs=dialogs, rows=[GEE_MISSING],
                             running=frozenset({mask_inflight_key("mc", "forest")}), stages={key: "align"})
        try:
            assert seen["rows"][0]["cells"][2]["label"] == t("steps.prepare.state_aligning")
            _action(seen, "forest", "info")()
            # the Details lead reads the running state, not "To download"
            assert dialogs["details"]["details"]["lead"] == t("steps.prepare.state_aligning")
        finally:
            rc.close()

    asyncio.run(main())


# ---------------------------------------------------------------- the modal, rendered
@solara.component
def _StubRasterInput(gee_interface, origin, asset, path, asset_types=("IMAGE", "IMAGE_COLLECTION"),
                     disabled=False):
    """Stands in for pysepal's pickers: the test sets ``asset`` or ``path``
    itself. It draws one empty marker (``dv-stub-raster-input``), so its place
    among the form's fields can be asserted."""
    _StubRasterInput.seen = SimpleNamespace(origin=origin, asset=asset, path=path, disabled=disabled)
    rv.Html(tag="div", class_="dv-stub-raster-input")


def _gee(monkeypatch, bands, release=None):
    """An Earth Engine stand-in whose band list of any asset is ``bands``, read
    once ``release`` (when given) is set."""
    from gui.widget import raster_input

    monkeypatch.setattr(raster_input.user_raster, "band_list_request", lambda ref, kind: ("BANDS", ref, kind))

    async def get_info_async(obj):
        if release is not None:
            await release.wait()
        return list(bands)

    return SimpleNamespace(get_info_async=get_info_async)


def _modal(state, open_, added, saved, editing=None, gee=None):
    return mask_modal.MaskModal(open_=open_, app_state=state, gee_interface=gee or object(),
                                on_add=lambda fields, mode: added.append((fields, mode)),
                                on_save=lambda name, changes: saved.append((name, changes)), editing=editing)


def _source(rc):
    return rc.find(v.Select, label=t("steps.raster_input.field_source")).widgets[0]


async def _settle(cond, timeout=10.0):
    """Poll ``cond`` until it holds or a wall-clock deadline passes (a count of
    sleeps gives up early on a loaded machine)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        if cond():
            return True
    return False


def _name_field(rc):
    return rc.find(v.TextField, label=t("steps.prepare.mask_name")).widgets[0]


def _submit_btn(rc, label=None):
    label = label or t("steps.prepare.mask_add")
    [btn] = [b for b in rc.find(v.Btn).widgets if label in b.children]
    return btn


def _dialog_cancel(rc):
    [btn] = [b for b in rc.find(v.Btn).widgets if t("common.cancel") in b.children
             and not any(isinstance(c, v.ProgressCircular) for c in b.children)]
    return btn


def _shown(rc, text):
    return [w for w in rc.find(v.Html).widgets if text in str(w.children)]


def _defaults(rc):
    """The buttons Enter would press (gui/widget/enter_key)."""
    return [b for b in rc.find(v.Btn).widgets if ENTER_DEFAULT in (b.class_ or "").split()]


def _icons(btn):
    return [c.children[0] for c in btn.children if isinstance(c, v.Icon)]


def test_a_geotiff_mask_is_validated_added_inside_the_task_and_the_modal_closes(tmp_path, monkeypatch):
    path = _tif(tmp_path / "forest.tif")
    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    state, open_, added = _state(_p()), solara.reactive(True), []
    loop_thread = []

    def on_add(fields, mode):
        loop_thread.append(threading.current_thread())
        added.append((fields, mode))

    async def main():
        box, rc = reacton.render(mask_modal.MaskModal(open_=open_, app_state=state, gee_interface=object(),
                                                      on_add=on_add, on_save=None), handle_error=False)
        try:
            _name_field(rc).v_model = "probability"
            _source(rc).v_model = "file"
            _StubRasterInput.seen.path.set(str(path))
            bands = lambda: rc.find(v.Select, label=t("steps.prepare.mask_band")).widgets  # noqa: E731
            assert await _settle(lambda: bands() and len(bands()[0].items) == 2)  # the file's bands, by index
            assert await _settle(lambda: bands()[0].v_model == 1)  # no band looks like a mask: the first
            bands()[0].v_model = 2
            rc.find(v.RadioGroup, v_model="keep").widgets[0].v_model = "exclude"
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            [(fields, mode)] = added
            assert (fields["name"], fields["origin"], fields["ref"], fields["band"], mode) == (
                "probability", "file", str(path), 2, "exclude")
            assert loop_thread == [threading.current_thread()]  # the task, on the loop
        finally:
            rc.close()

    asyncio.run(main())


def test_a_refused_mask_shows_its_error_inline_and_can_be_sent_again(monkeypatch):
    asked = []

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        return {"error": "steps.alerts.err_missing_band"}

    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(mask_modal, "validate_mask", fake_validate)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _name_field(rc).v_model = "w"
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: bool(_shown(rc, t("steps.alerts.err_missing_band"))))
            assert open_.value is True and added == []
            _submit_btn(rc).fire_event("click", None)  # re-armed
            assert await _settle(lambda: len(asked) == 2)
        finally:
            rc.close()

    asyncio.run(main())


def test_an_exception_from_on_add_is_shown_inline(monkeypatch):
    async def fake_validate(gee_interface, project, **kw):
        return {"fields": {"name": kw["name"]}}

    def on_add(fields, mode):
        raise ValueError(t("steps.prepare.mask_name_duplicate"))

    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(mask_modal, "validate_mask", fake_validate)
    state, open_ = _state(_p()), solara.reactive(True)

    async def main():
        box, rc = reacton.render(mask_modal.MaskModal(open_=open_, app_state=state, gee_interface=object(),
                                                      on_add=on_add, on_save=None), handle_error=False)
        try:
            _name_field(rc).v_model = "w"
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: bool(_shown(rc, t("steps.prepare.mask_name_duplicate"))))
            assert open_.value is True
        finally:
            rc.close()

    asyncio.run(main())


def test_an_edit_saves_its_changes_through_on_save(tmp_path, monkeypatch):
    asked = []

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        return {"fields": dict(name="forest", origin="gee", ref=kw["ref"], asset_type="IMAGE", band=kw["band"],
                               native=NativeGrid(crs="EPSG:4326", scale=30.0))}

    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(mask_modal, "validate_mask", fake_validate)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []
    gee = _gee(monkeypatch, ["a", "b"])

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved, editing="forest", gee=gee), handle_error=False)
        try:
            assert _name_field(rc).disabled is True and _name_field(rc).v_model == "forest"
            assert not rc.find(v.RadioGroup, v_model="keep").widgets  # an edit has no mode: periods choose it
            assert await _settle(lambda: not _submit_btn(rc, t("steps.alerts.save")).disabled)  # bands read
            _submit_btn(rc, t("steps.alerts.save")).fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            assert asked[0]["editing"] == "forest" and asked[0]["ref"] == "UMD/x" and asked[0]["band"] == "b"
            [(name, changes)] = saved
            assert name == "forest" and "name" not in changes and changes["ref"] == "UMD/x" and added == []
        finally:
            rc.close()

    asyncio.run(main())


def _hanging(monkeypatch):
    asked, done, release = [], [], asyncio.Event()

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        await release.wait()
        done.append(kw)
        return {"fields": {"name": kw["name"]}}

    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    monkeypatch.setattr(mask_modal, "validate_mask", fake_validate)
    return asked, done, release


@pytest.mark.parametrize("close", ["esc", "cancel"])
def test_closing_the_dialog_during_a_check_adds_nothing(monkeypatch, close):
    asked, done, release = _hanging(monkeypatch)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []
    gee = _gee(monkeypatch, ["a", "b"])

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved, gee=gee), handle_error=False)
        try:
            _name_field(rc).v_model = "late"
            _StubRasterInput.seen.asset.set({"asset_id": "users/x/m", "type": "IMAGE", "column": "ALL",
                                             "value": None})
            assert await _settle(lambda: bool(rc.find(v.Select, label=t("steps.prepare.mask_band")).widgets))
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            # the fields are frozen while their values are checked
            assert _name_field(rc).disabled is True and _StubRasterInput.seen.disabled is True
            assert _source(rc).disabled is True
            assert rc.find(v.Select, label=t("steps.prepare.mask_band")).widgets[0].disabled is True
            assert rc.find(v.RadioGroup, v_model="keep").widgets[0].disabled is True
            assert _defaults(rc) == []  # Review Focus 1: mid-check, Enter presses nothing
            if close == "esc":
                rc.find(v.Dialog).widgets[0].fire_event("keydown.esc", None)
            else:
                _dialog_cancel(rc).fire_event("click", None)
            assert open_.value is False
            release.set()
            await asyncio.sleep(0.1)
            assert added == [] and done == []  # cancelled at its await
        finally:
            rc.close()

    asyncio.run(main())


def test_a_check_that_ends_after_the_dialog_closed_adds_nothing(monkeypatch):
    import solara.tasks

    asked, done, release = _hanging(monkeypatch)
    monkeypatch.setattr(solara.tasks.TaskAsyncio, "cancel", lambda self: None)  # the unmount cannot stop it
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _name_field(rc).v_model = "late"
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            rc.find(v.Dialog).widgets[0].fire_event("keydown.esc", None)
            release.set()
            assert await _settle(lambda: done)
            await asyncio.sleep(0.05)
            assert added == []
        finally:
            rc.close()

    asyncio.run(main())


@pytest.mark.parametrize("change", ["aoi", "period", "project"])
def test_a_project_change_during_the_check_refuses_inline(monkeypatch, change):
    asked, done, release = _hanging(monkeypatch)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _name_field(rc).v_model = "moved"
            await asyncio.sleep(0.05)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            if change == "aoi":
                state.update_project("mc", lambda live: setattr(
                    live, "aoi", live.aoi.model_copy(update={"digest": "d2"})))
            elif change == "period":
                state.update_project("mc", lambda live: (setattr(live, "periods", {**live.periods, MAR.id: MAR}),
                                                         setattr(live, "active_period", MAR.id)))
            else:
                other = _p()
                other.name = "other"
                state.load_project_state(other)
            release.set()
            assert await _settle(lambda: bool(_shown(rc, t("steps.prepare.mask_err_changed"))))
            assert added == [] and open_.value is True
        finally:
            rc.close()

    asyncio.run(main())


def test_the_open_form_survives_a_worker_publish(tmp_path, monkeypatch):
    """Global Constraints: a worker's publish re-renders the open form in the
    worker's thread, where no task may (re)start."""
    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []
    errors = []

    async def main():
        box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            _name_field(rc).v_model = "x"
            _source(rc).v_model = "file"
            _StubRasterInput.seen.path.set(str(_tif(tmp_path / "w.tif")))
            await asyncio.sleep(0.1)

            def worker():
                try:
                    state.update_project("mc", lambda live: set_period_mask(live, FEB.id, "forest", on=False))
                except Exception as exc:  # noqa: BLE001 - reported below
                    errors.append(exc)

            th = threading.Thread(target=worker)
            th.start()
            th.join()
            assert errors == []
            assert _name_field(rc).v_model == "x"
        finally:
            rc.close()

    asyncio.run(main())


def test_the_mask_form_sits_on_the_form_frame(monkeypatch):
    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, added, saved), handle_error=False)
        try:
            dialog = rc.find(v.Dialog).widget
            assert (dialog.max_width, dialog.persistent, dialog.eager) == ("560px", True, True)
            assert (_name_field(rc).hint, _name_field(rc).persistent_hint) == (
                t("steps.prepare.mask_name_hint"), True)
            fieldset = rc.find(v.Html, tag="fieldset").widget
            assert fieldset.children[0].children == [t("steps.prepare.mask_mode_label")]
            assert rc.find(v.RadioGroup, v_model="keep").widget in fieldset.children
            add = _submit_btn(rc)
            assert _defaults(rc) == [add] and _icons(add) == ["mdi-plus"]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_mask_edit_has_a_save_button_and_no_mode_fieldset(monkeypatch):
    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    state, open_, added, saved = _state(_p()), solara.reactive(True), [], []

    async def main():
        _box, rc = reacton.render(_modal(state, open_, added, saved, editing="forest"), handle_error=False)
        try:
            save = _submit_btn(rc, t("steps.alerts.save"))
            assert _defaults(rc) == [save] and _icons(save) == ["mdi-content-save-outline"]
            assert not rc.find(v.Html, tag="fieldset").widgets
        finally:
            rc.close()

    asyncio.run(main())


def test_the_card_renders_nothing_once_the_project_is_closed(monkeypatch):
    """The card subscribes to the project: closing it re-renders the card
    (maybe before its step unmounts it), which must not fail."""
    state = _state(_p())

    async def main():
        rc, seen, modal, _ = _card(monkeypatch, state)
        try:
            state.close_project_state()
            assert state.project.value is None
        finally:
            rc.close()

    asyncio.run(main())


@pytest.mark.parametrize("flip", ["off", "exclude"])
def test_a_refused_switch_puts_the_cell_back(monkeypatch, flip):
    """When the switch is refused (the project changed meanwhile) the props
    are unchanged, so nothing was pushed back and the cell kept the user's
    flip; the card now remounts the row's cells on what the project holds."""
    state = _state(_p())
    monkeypatch.setattr(masks_card, "MaskModal", lambda **kw: None)
    monkeypatch.setattr(masks_card, "ConfirmDialog", lambda **kw: None)
    monkeypatch.setattr(masks_card, "switch_mask", lambda *a, **k: False)
    period = state.project.value.periods[FEB.id]
    map_ = _Map()
    rows = [MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep")]

    @reacton.component
    def Page():
        NotificationProvider()
        masks_card.MasksCard(app_state=state, map_=map_, gee_interface=None, layers=MapLayers(map_), period=period,
                             rows=rows, running=frozenset(), errors={}, on_run=lambda n: None,
                             on_rerun=lambda n: None)

    async def main():
        box, rc = reacton.render(Page(), handle_error=False)
        try:
            rc.find(v.Select).widget.v_model = flip  # the user's choice, refused
            assert rc.find(v.Select).widget.v_model == "keep"
        finally:
            rc.close()

    asyncio.run(main())
