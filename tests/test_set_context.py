"""Analysis reads and updates the active alert set through ``SetContext``
(spec §10): the set's folder, combined raster and report template under the
recipe-era attribute names, the review position and the reviewed count.

The Analysis sites are driven for real: ``view_actual_alert`` +
``select_start_alert`` on a real ``AppState``, the export's store save, and the
image selector's map callback (Earth Engine builds the imagery layers there, so
those tests take the ``gee`` fixture; the tile server is a stand-in).
"""

from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState

SET_DIR = ("periods", "per", "sets", "glad-l")


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now(), report_template="/t.docx")
    p.alert_sets["x"] = AlertSet(id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="periods/per/sets/glad-l/alert_db.parquet",
                                 combined_rel_path="periods/per/sets/glad-l/c.tif",
                                 footprint_rel_path="periods/per/sets/glad-l/footprint.tif",
                                 fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    s.new_project_state(p)
    return s


def test_paths_and_template(tmp_path, monkeypatch):
    ctx = SetContext(_state(tmp_path, monkeypatch))
    assert ctx.recipe_folder_path == str(tmp_path / "d" / "periods" / "per" / "sets" / "glad-l")
    assert ctx.combined_path == tmp_path / "d" / "periods" / "per" / "sets" / "glad-l" / "c.tif"
    assert ctx.custom_report_template == "/t.docx"


def test_no_active_set_is_empty():
    s = AppState()
    ctx = SetContext(s)
    assert ctx.recipe_folder_path == "" and ctx.combined_path is None and ctx.custom_report_template is None


def test_remember_position_and_record_review(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    ctx.remember_position(7)
    assert s.project.value.alert_sets["x"].last_alert_index == 7
    ctx.record_review(pd.DataFrame({"status": ["Confirmed", "Not reviewed", "False Positive"]}))
    assert s.project.value.alert_sets["x"].n_reviewed == 2


def test_the_context_follows_the_open_project(tmp_path, monkeypatch):
    """The Analysis layout is built once and outlives project switches."""
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    s.close_project_state()
    assert ctx.alert_set is None and ctx.recipe_folder_path == "" and ctx.combined_path is None
    ctx.remember_position(3)  # nothing open: a no-op, not an error
    ctx.record_review(pd.DataFrame({"status": ["Confirmed"]}))


def test_an_unchanged_position_or_count_publishes_nothing(tmp_path, monkeypatch):
    """Every publish is an auto-save of the manifest: re-selecting the saved
    position (a set load does this) must not rewrite it."""
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    ctx.remember_position(4)
    ctx.record_review(pd.DataFrame({"status": ["Confirmed"]}))
    published = []
    s.project.subscribe(published.append)
    ctx.remember_position(4)
    ctx.record_review(pd.DataFrame({"status": ["Need revision"]}))
    assert published == []


def test_another_sets_alerts_do_not_write_onto_the_active_set(tmp_path, monkeypatch):
    """``alerts_gdf`` and the active set are separate reactives: while the
    alerts on hand are another set's (Integrate records a set before it
    publishes its alerts), their position and count are not this set's."""
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    other = pd.DataFrame({"status": ["Confirmed", "Confirmed"], "set_id": ["y", "y"]})
    s.alerts_gdf.set(other)
    ctx.remember_position(1)
    ctx.record_review(other)
    live = s.project.value.alert_sets["x"]
    assert live.last_alert_index == 0 and live.n_reviewed == 0


# ---------------------------------------------------------------------------
# The review position: view_actual_alert remembers it, a set load restores it
# ---------------------------------------------------------------------------
def _alerts(n, set_id="x"):
    return pd.DataFrame({
        "alert_date_min": [2026.001] * n, "alert_date_max": [2026.010] * n,
        "alert_sources": ["GLAD-L"] * n, "status": ["Not reviewed"] * n, "set_id": [set_id] * n,
    })


def _wire_analysis(s, ctx):
    """The two subscriptions ``build_analysis_layout`` registers, on stand-in
    widgets. Only the info card and the position run; the map half (bbox
    overlay, Earth Engine grid) needs geometries these rows do not carry and
    stays inside ``view_actual_alert``'s own guard."""
    from gui.tile.analysis import analysis_tile

    widgets = SimpleNamespace(selected_alert_info=SimpleNamespace(children=[]))
    id_field = SimpleNamespace(v_model=None)
    scratch = SimpleNamespace(defo_dl_layer=None)
    s.actual_alert_id.subscribe(lambda *_: analysis_tile.view_actual_alert(widgets, s, scratch, ctx, id_field))
    s.alerts_gdf.subscribe(lambda *_: analysis_tile.select_start_alert(s, ctx))
    return id_field


def test_navigating_remembers_the_position_on_the_set(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    id_field = _wire_analysis(s, ctx)
    s.alerts_gdf.set(_alerts(10))
    s.go_to(5)
    assert id_field.v_model == 5
    assert s.project.value.alert_sets["x"].last_alert_index == 5


def test_a_loaded_set_reopens_at_its_saved_position(tmp_path, monkeypatch):
    """Drift note (Task 21): a set load publishes its alerts first and reads
    ``last_alert_index`` after (Task 22's loader). The auto-select runs in
    between; when it went to alert 0, ``view_actual_alert`` saved 0 over the
    saved position before the loader could read it."""
    s = _state(tmp_path, monkeypatch)
    s.project.value.alert_sets["x"].last_alert_index = 7  # as saved on disk
    ctx = SetContext(s)
    _wire_analysis(s, ctx)

    gdf = _alerts(10)
    s.alerts_gdf.set(gdf)  # the loader, then:
    saved = s.project.value.alert_sets["x"].last_alert_index
    s.actual_alert_id.set(min(saved, len(gdf) - 1))

    assert s.actual_alert_id.value == 7
    assert s.project.value.alert_sets["x"].last_alert_index == 7


def test_without_a_saved_position_the_first_alert_is_selected():
    """The recipe-era models and the test fakes carry no ``alert_set``."""
    from gui.tile.analysis import analysis_tile

    s = AppState()
    s.alerts_gdf.set(_alerts(3))
    analysis_tile.select_start_alert(s, SimpleNamespace(recipe_folder_path=""))
    assert s.actual_alert_id.value == 0


# ---------------------------------------------------------------------------
# Review saves land in the set's folder and keep its reviewed count
# ---------------------------------------------------------------------------
def test_a_review_save_writes_the_sets_store_and_its_reviewed_count(tmp_path, monkeypatch, ws):
    import geopandas as gpd
    from shapely.geometry import box

    from gui.tile.analysis.export import _save_alerts_to_gdf

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    gdf = gpd.GeoDataFrame({"status": ["Confirmed", "Not reviewed", "Need revision"]},
                           geometry=[box(0, 0, 1, 1)] * 3, crs="EPSG:4326")

    _save_alerts_to_gdf(gdf, ctx, ws)

    assert (tmp_path / "d" / "periods" / "per" / "sets" / "glad-l" / "alert_db.parquet").exists()
    assert s.project.value.alert_sets["x"].n_reviewed == 2


# A review Save pins its set before the Earth Engine admin lookups, which block
# for seconds: the live context follows whatever is opened meanwhile.
def _alert_set(sid, slug):
    rel = f"periods/per/sets/{slug}"
    return AlertSet(id=sid, period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                    params=AlertSetParams(), store_rel_path=f"{rel}/alert_db.parquet",
                    combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
                    fingerprint="f", created_at=datetime.now())


def _store(tmp_path, project, slug, content=None):
    """A set's ``alert_db.parquet``; with ``content``, one already written."""
    path = tmp_path.joinpath(project, "periods", "per", "sets", slug, "alert_db.parquet")
    if content is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return path


def _review_alerts(set_id="x", n=2):
    """A set's alerts as Integrate stores them: unreviewed, three geometries."""
    import geopandas as gpd
    from shapely.geometry import Point, box

    polys = [box(i, 0, i + 1, 1) for i in range(n)]
    gdf = gpd.GeoDataFrame({"status": ["Not reviewed"] * n, "set_id": [set_id] * n},
                           geometry=polys, crs="EPSG:4326")
    gdf["bounding_box"] = gpd.GeoSeries(polys, crs="EPSG:4326")
    gdf["point"] = gpd.GeoSeries([Point(i + 0.5, 0.5) for i in range(n)], crs="EPSG:4326")
    for col in ("alert_polygon", "description", "before_img", "after_img",
                "before_img_info", "after_img_info", "admin1", "admin2", "admin3", "area_ha"):
        gdf[col] = pd.Series([None] * n, dtype="object")
    return gdf


class _AdminLookups:
    """Stands in for ``export.ee``: the FAO/GAUL lookup chain. Its first
    blocking ``getInfo`` runs ``during``, what the user does while it blocks."""

    def __init__(self, during=None):
        self.during = during
        self.calls = 0

    def __getattr__(self, name):  # FeatureCollection, String, filterBounds, iterate...
        return lambda *a, **k: self

    def getInfo(self):  # noqa: N802 - Earth Engine's name
        self.calls += 1
        during, self.during = self.during, None
        if during is not None:
            during()
        return "Brasil, "


def _save(s, ctx, ws, monkeypatch, lookups=None, alert_id=0):
    """Press Save on alert ``alert_id`` (Confirmed, no drawn polygon), for real."""
    from deforestation_alerts.message import cm
    from gui.tile.analysis import export

    monkeypatch.setattr(export, "ee", lookups or _AdminLookups())
    widgets = SimpleNamespace(
        confirmation_select=SimpleNamespace(v_model=cm.analysis_tile.questionarie.confirmation_yes),
        driver_combobox=SimpleNamespace(v_model=["Agriculture"]),
        selected_img_before_info_list=["Planet NICFI", 1, None, "before", "_b"],
        selected_img_after_info_list=["Planet NICFI", 2, None, "after", "_a"],
        actual_bb=SimpleNamespace(geometry=lambda: None),
    )
    s.actual_alert_id.set(alert_id)
    return export._save_attributes(widgets, s, SimpleNamespace(defo_dl_layer=None), ctx, ws)


def _saved_status(path):
    return pd.read_parquet(path, columns=["status"])["status"].tolist()


@pytest.mark.parametrize("published", [False, True], ids=["recorded", "recorded-and-published"])
def test_a_set_activated_mid_save_refuses_the_write(tmp_path, monkeypatch, ws, published):
    """An Integrate that finishes during the lookups makes its new set active
    (``apply_result``), then publishes its alerts. The Save no longer writes
    for the set it started on (ruling: a warning, not a cross-write): neither
    the reviewed set's store nor the new set's is written, nor either count."""
    from deforestation_alerts.message import cm
    from gui.scripts.integrate_jobs import apply_result
    from gui.tile.analysis.export import SaveRefused

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x"))
    reviewed = _store(tmp_path, "d", "glad-l", b"set x's store")
    fresh = _store(tmp_path, "d", "glad-s2", b"set y, freshly integrated")

    def _integrate_finishes():
        result = SimpleNamespace(artifacts={}, alert_set=_alert_set("y", "glad-s2"))
        assert s.update_project("d", lambda p: apply_result(p, result))
        if published:
            s.alerts_gdf.set(_review_alerts("y"))

    with pytest.raises(SaveRefused) as refused:
        _save(s, ctx, ws, monkeypatch, _AdminLookups(during=_integrate_finishes))

    assert str(refused.value) == str(cm.analysis_tile.export_labels.save_set_changed)
    assert s.project.value.active_set == "y"
    assert fresh.read_bytes() == b"set y, freshly integrated"
    assert reviewed.read_bytes() == b"set x's store"
    sets = s.project.value.alert_sets
    assert sets["x"].n_reviewed == 0 and sets["y"].n_reviewed == 0


@pytest.mark.parametrize("switch", ["close", "open-another"])
def test_a_project_switch_mid_save_refuses_the_write(tmp_path, monkeypatch, ws, switch):
    """The set the Save started on is no longer the open project's active set:
    nothing is written, in either project. Set ids repeat across projects
    (period + sources), so project e's set "x" is neither written nor counted."""
    from gui.tile.analysis.export import SaveRefused

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x"))
    reviewed = _store(tmp_path, "d", "glad-l", b"project d's store")
    other = _store(tmp_path, "e", "glad-l", b"project e's store")

    def _switch():
        if switch == "close":
            s.close_project_state()
        else:
            e = Project(name="e", created_at=datetime.now(), updated_at=datetime.now())
            e.alert_sets["x"] = _alert_set("x", "glad-l")
            e.active_set = "x"
            s.new_project_state(e)

    with pytest.raises(SaveRefused):
        _save(s, ctx, ws, monkeypatch, _AdminLookups(during=_switch))

    assert other.read_bytes() == b"project e's store"
    assert reviewed.read_bytes() == b"project d's store"
    if switch == "open-another":
        assert s.project.value.name == "e" and s.project.value.alert_sets["x"].n_reviewed == 0


def _loaded(s, ws, alerts):
    """``alerts`` as the shell's loader publishes them: written to the active
    set's store, read back from it, and stamped with that store's generation."""
    from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
    from gui.scripts import set_store

    store = set_store.active_store_path(s.project.value)
    save_gdf_to_parquet(alerts, ws, str(store))
    gdf = set_store.load_active_alerts(s.project.value, ws)
    s.alerts_gdf.set(gdf)
    return gdf, store


def _rerun(s, ws, store, alerts):
    """What a re-run of the same set does: swap its store in (run_integration),
    record the new set (apply_result) and publish its alerts."""
    from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
    from gui.scripts.integrate_jobs import _publish_alerts, apply_result
    from gui.scripts import set_store

    save_gdf_to_parquet(alerts, ws, str(store))
    set_store.stamp_generation(alerts, store)
    new_set = _alert_set("x", "glad-l").model_copy(update={"n_alerts": len(alerts), "n_reviewed": 0})
    result = SimpleNamespace(artifacts={}, alert_set=new_set, alerts=alerts)
    assert s.update_project("d", lambda p: apply_result(p, result))
    return result, lambda: _publish_alerts(s, "d", result)


@pytest.mark.parametrize("published", [False, True], ids=["recorded", "recorded-and-published"])
def test_a_same_set_rerun_mid_save_refuses_the_write(tmp_path, monkeypatch, ws, published):
    """A re-run of the same period and sources keeps the set id, so the id
    checks pass while the run swaps a new store in under the alerts on hand.
    The Save that started on the old alerts (and blocked on the admin
    lookups) must not write them, plus its review, over the re-run's store:
    the alerts carry the generation of the store they were read from, and
    the store on disk is another one by the time the write would land."""
    from deforestation_alerts.message import cm
    from gui.tile.analysis.export import SaveRefused

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    _, store = _loaded(s, ws, _review_alerts("x", n=2))
    new = _review_alerts("x", n=5)

    def _rerun_finishes():
        _, publish = _rerun(s, ws, store, new)
        if published:
            assert publish()

    with pytest.raises(SaveRefused) as refused:
        _save(s, ctx, ws, monkeypatch, _AdminLookups(during=_rerun_finishes))

    assert str(refused.value) == str(cm.analysis_tile.export_labels.save_store_changed)
    assert _saved_status(store) == ["Not reviewed"] * 5  # the re-run's store, untouched
    rec = s.project.value.alert_sets["x"]
    assert rec.n_alerts == 5 and rec.n_reviewed == 0
    if published:
        assert s.alerts_gdf.value is new and _save(s, ctx, ws, monkeypatch) is True  # the new alerts save fine
        assert _saved_status(store)[0] == "Confirmed"


def test_a_store_swapped_in_during_the_serialization_is_not_overwritten(tmp_path, monkeypatch, ws):
    """The generation is checked again right before the serialized file is
    moved onto the store, so a swap landing after the first check (during
    the parquet write, seconds for a large set) is caught too."""
    import geopandas as gpd

    from gui.tile.analysis.export import SaveRefused, _save_alerts_to_gdf

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    old, store = _loaded(s, ws, _review_alerts("x", n=2))
    old.loc[0, "status"] = "Confirmed"
    new = _review_alerts("x", n=5)
    real = gpd.GeoDataFrame.to_parquet

    def _swap_during_serialization(self, path, *args, **kwargs):
        monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", real)  # once
        _rerun(s, ws, store, new)
        return real(self, path, *args, **kwargs)

    monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", _swap_during_serialization)
    with pytest.raises(SaveRefused):
        _save_alerts_to_gdf(old, ctx, ws)

    assert _saved_status(store) == ["Not reviewed"] * 5
    assert s.project.value.alert_sets["x"].n_reviewed == 0


def test_consecutive_saves_of_the_same_alerts_all_land(tmp_path, monkeypatch, ws):
    """Each Save rewrites the store: the alerts are re-stamped with the store
    they just wrote, so the next Save of them is not refused. The stamp
    survives the copy the save effect rebinds ``alerts_gdf`` to."""
    from gui.scripts import set_store

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    gdf, store = _loaded(s, ws, _review_alerts("x", n=2))

    assert _save(s, ctx, ws, monkeypatch) is True
    assert gdf.attrs[set_store.STORE_GENERATION] == set_store.store_generation(store)
    s.alerts_gdf.set(gdf.copy())  # export._apply_save
    assert _save(s, ctx, ws, monkeypatch, alert_id=1) is True

    assert _saved_status(store) == ["Confirmed", "Confirmed"]
    assert s.project.value.alert_sets["x"].n_reviewed == 2


def test_the_same_project_reopened_mid_save_still_takes_the_review(tmp_path, monkeypatch, ws):
    """Reopening the project installs a new copy of it, but its active set is
    still the one the Save started on: the review is written."""
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x"))

    def _reopen():
        s.load_project_state(s.project.value.model_copy(deep=True))

    assert _save(s, ctx, ws, monkeypatch, _AdminLookups(during=_reopen)) is True

    assert _saved_status(_store(tmp_path, "d", "glad-l")) == ["Confirmed", "Not reviewed"]
    assert s.project.value.alert_sets["x"].n_reviewed == 1


def test_a_save_with_no_image_selected_still_persists(tmp_path, monkeypatch, ws):
    """Before any Planet/S2/Landsat image is picked for the alert under
    review, ``selected_img_before_info_list`` / ``_after`` are still ``None``
    (widgets.py:275-276; only populated by imagery.py:214/216 once an image
    is chosen). Save must not crash subscripting them (fix-round regression
    for the Task 23 review finding) and persists the review with the
    before/after image columns left empty."""
    from deforestation_alerts.message import cm
    from gui.tile.analysis import export

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x"))

    widgets = SimpleNamespace(
        confirmation_select=SimpleNamespace(v_model=cm.analysis_tile.questionarie.confirmation_yes),
        driver_combobox=SimpleNamespace(v_model=["Agriculture"]),
        selected_img_before_info_list=None,
        selected_img_after_info_list=None,
        actual_bb=SimpleNamespace(geometry=lambda: None),
    )
    monkeypatch.setattr(export, "ee", _AdminLookups())
    s.actual_alert_id.set(0)

    assert export._save_attributes(
        widgets, s, SimpleNamespace(defo_dl_layer=None), ctx, ws
    ) is True

    saved = pd.read_parquet(
        _store(tmp_path, "d", "glad-l"),
        columns=["status", "before_img", "after_img", "before_img_info", "after_img_info"],
    )
    assert saved.loc[0, "status"] == "Confirmed"
    assert saved.loc[0, ["before_img", "after_img", "before_img_info", "after_img_info"]].isna().all()
    assert s.project.value.alert_sets["x"].n_reviewed == 1


def test_another_sets_alerts_are_not_saved_into_the_active_set(tmp_path, monkeypatch, ws):
    """Integrate records a new set before it publishes that set's alerts: for
    that moment the alerts on hand are the previous set's. A Save is then
    refused (the task's warning toast) before it looks anything up or changes
    a row."""
    from deforestation_alerts.message import cm
    from gui.tile.analysis.export import SaveRefused

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("y"))
    store = _store(tmp_path, "d", "glad-l", b"set x's store")

    lookups = _AdminLookups()
    with pytest.raises(SaveRefused) as refused:
        _save(s, ctx, ws, monkeypatch, lookups)

    assert str(refused.value) == str(cm.analysis_tile.export_labels.save_other_set)
    assert store.read_bytes() == b"set x's store"
    assert lookups.calls == 0
    assert s.alerts_gdf.value["status"].tolist() == ["Not reviewed", "Not reviewed"]
    assert s.project.value.alert_sets["x"].n_reviewed == 0


def test_the_store_writer_refuses_another_sets_alerts(tmp_path, monkeypatch, ws):
    """The guard sits right before the write too, for every caller."""
    from gui.tile.analysis.export import SaveRefused, _save_alerts_to_gdf

    s = _state(tmp_path, monkeypatch)
    store = _store(tmp_path, "d", "glad-l", b"set x's store")
    with pytest.raises(SaveRefused):
        _save_alerts_to_gdf(_review_alerts("y"), SetContext(s), ws)
    assert store.read_bytes() == b"set x's store"


def test_the_store_writer_refuses_a_set_that_is_no_longer_active(tmp_path, monkeypatch, ws):
    """A writer handed the set it pinned earlier checks that set is still the
    active one before it writes."""
    from gui.scripts.integrate_jobs import apply_result
    from gui.tile.analysis.export import SaveRefused, _save_alerts_to_gdf

    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    target = ctx.pin()
    store = _store(tmp_path, "d", "glad-l", b"set x's store")
    result = SimpleNamespace(artifacts={}, alert_set=_alert_set("y", "glad-s2"))
    assert s.update_project("d", lambda p: apply_result(p, result))

    with pytest.raises(SaveRefused):
        _save_alerts_to_gdf(_review_alerts("x"), ctx, ws, folder=str(target.folder), target=target)
    assert store.read_bytes() == b"set x's store"


def test_a_pin_is_the_active_set_as_it_was(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    ctx = SetContext(s)
    target = ctx.pin()
    s.close_project_state()
    assert target.project == "d" and target.alert_set.id == "x"
    assert target.folder == tmp_path.joinpath("d", *SET_DIR)
    assert ctx.pin() is None


# ---------------------------------------------------------------------------
# "Original Alert" is the set's combined raster, band 3
# ---------------------------------------------------------------------------
class _Map:
    """Records the Earth Engine layers the callback adds."""

    def __init__(self):
        self.ee_layers = []

    def addLayer(self, image, vis, name, shown=True, opacity=1.0):  # noqa: N802 - SepalMap's name
        self.ee_layers.append(name)


def _pick_image(ctx, monkeypatch, add_raster_layer):
    import ee

    from gui.scripts import raster_map
    from gui.tile.analysis import imagery

    monkeypatch.setattr(raster_map, "add_raster_layer", add_raster_layer)
    map_ = _Map()
    widgets = SimpleNamespace(
        imgSelection1=SimpleNamespace(disabled=False), imgSelection2=SimpleNamespace(disabled=False),
        actual_alert_grid=ee.Geometry.Rectangle([-55.10, -10.10, -55.00, -10.00]),
    )
    item = {"image_id": "projects/planet-nicfi/assets/basemaps/americas/"
                        "planet_medres_normalized_analytic_2024-01_mosaic", "source": "Planet NICFI"}
    imagery.image_slider_map_callback(item, map_, 0, widgets, ctx)
    return map_, widgets


def _combined(tmp_path):
    path = tmp_path.joinpath("d", *SET_DIR, "c.tif")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    return path


def test_original_alert_is_the_sets_combined_raster_band_3(tmp_path, monkeypatch, gee):
    ctx = SetContext(_state(tmp_path, monkeypatch))
    combined = _combined(tmp_path)
    calls = []

    def _add(map_, path, **kw):
        calls.append((map_, path, kw))
        return SimpleNamespace(visible=True)

    map_, widgets = _pick_image(ctx, monkeypatch, _add)

    assert map_.ee_layers == ["True Color", "False Color"]
    [(on_map, path, kw)] = calls
    assert on_map is map_ and path == combined
    assert kw == {"key": "original-alert", "name": "Original Alert", "band": 3, "opacity": 0.5}
    assert not widgets.imgSelection1.disabled and not widgets.imgSelection2.disabled


def test_the_original_alert_layer_starts_hidden(tmp_path, monkeypatch, gee):
    ctx = SetContext(_state(tmp_path, monkeypatch))
    _combined(tmp_path)
    layer = SimpleNamespace(visible=True)
    _pick_image(ctx, monkeypatch, lambda *a, **k: layer)
    assert layer.visible is False


def test_no_original_alert_without_the_combined_raster(tmp_path, monkeypatch, gee):
    """A moved or deleted set folder, or a recipe-era model without a set."""
    calls = []
    for ctx in (SetContext(_state(tmp_path, monkeypatch)), SimpleNamespace(filtered_alert_raster=None)):
        map_, _ = _pick_image(ctx, monkeypatch, lambda *a, **k: calls.append(a))
        assert map_.ee_layers == ["True Color", "False Color"]
    assert calls == []


def test_a_failed_original_alert_layer_leaves_the_selectors_usable(tmp_path, monkeypatch, gee):
    """Ruling #85: the tile server starts in the slide click handler; a failure
    there must not leave the image selectors disabled."""
    ctx = SetContext(_state(tmp_path, monkeypatch))
    _combined(tmp_path)

    def _boom(*a, **k):
        raise OSError("tile server did not start")

    map_, widgets = _pick_image(ctx, monkeypatch, _boom)
    assert map_.ee_layers == ["True Color", "False Color"]
    assert not widgets.imgSelection1.disabled and not widgets.imgSelection2.disabled
