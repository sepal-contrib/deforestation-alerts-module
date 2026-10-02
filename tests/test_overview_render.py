"""Overview on the active alert set (spec §9.2, §9.3), rendered.

``OverviewTile`` is rendered with ``reacton.render`` against a real
``AppState``/``Project``, a real ``SepalMap(gee=False)`` and a real
``MapLayers``. The view reads; it draws nothing on the map, so the status
summary, the set label, the stale/missing banners and the export card are all
there is to see here -- the alert layers, the earlier sets and the
click-to-review popup are the shell's, and tests/test_alert_layers.py covers
them.

No Earth Engine, no browser.
"""

from datetime import date, datetime
from types import SimpleNamespace

import geopandas as gpd
import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.i18n import set_locale
from pysepal.mapping import SepalMap
from pysepal.solara.notifications import NotificationProvider
from shapely.geometry import box

from deforestation_alerts.engine.fingerprint import (
    fingerprint, harm_key, harmonized_inputs, raw_alert_inputs, raw_key, set_inputs,
)
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from deforestation_alerts.project.models import set_id as make_set_id
from gui.i18n import t
from gui.scripts.map_layers import MapLayers
from gui.store.state_manager import AppState
from gui.tile import overview_tile

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
SET_DIR = f"periods/{JAN.id}/sets/glad-l"


def _alerts(set_id=None):
    """Four alerts, one per status. ``set_id``: the column the engine writes
    (integrate.py), naming the set the alerts belong to."""
    gdf = gpd.GeoDataFrame(
        {"alert_id": [0, 1, 2, 3],
         "status": ["Confirmed", "False Positive", "Need revision", "Not reviewed"],
         "area_ha": [1.5, 0.25, 2.0, 0.75]},
        geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01), box(2, 2, 2.01, 2.01), box(3, 3, 3.01, 3.01)],
        crs="EPSG:4326",
    )
    if set_id is not None:
        gdf["set_id"] = set_id
    return gdf


def _project(tmp_path, name="d", *, current=True):
    """A project with one GLAD-L set for January, its store on disk.

    ``current``: its harmonized input is recorded, so the set's fingerprint
    matches (Status.CURRENT); otherwise it is out of date (Status.STALE). The
    raw and harmonized records carry their real fingerprints: ``set_status``
    also compares them with their current inputs.
    """
    now = datetime.now()
    p = Project(name=name, created_at=now, updated_at=now)
    add_predefined_source(p, "GLAD-L")
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    p.artifacts[raw_key(JAN.id, "GLAD-L")] = Artifact(
        key=raw_key(JAN.id, "GLAD-L"), kind="raw_alert", rel_path="r.tif",
        fingerprint=fingerprint(raw_alert_inputs(p, "GLAD-L", JAN)), inputs={}, content_id="r1", created_at=now)
    p.artifacts[harm_key(JAN.id, "GLAD-L")] = Artifact(
        key=harm_key(JAN.id, "GLAD-L"), kind="harmonized", rel_path="h.tif",
        fingerprint=fingerprint(harmonized_inputs(p, "GLAD-L", JAN)), inputs={}, content_id="h1", created_at=now)
    params = AlertSetParams()
    s = AlertSet(id=make_set_id(JAN.id, ["GLAD-L"]), period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                 params=params, store_rel_path=f"{SET_DIR}/alert_db.parquet", combined_rel_path=f"{SET_DIR}/c.tif",
                 footprint_rel_path=f"{SET_DIR}/footprint.tif", n_alerts=4, n_reviewed=3,
                 fingerprint=fingerprint(set_inputs(p, JAN, ["GLAD-L"], params)), created_at=now)
    if not current:
        p.artifacts[harm_key(JAN.id, "GLAD-L")].content_id = "h2"  # re-harmonized since
    p.alert_sets[s.id] = s
    p.active_set = s.id
    store = tmp_path / name / s.store_rel_path
    store.parent.mkdir(parents=True, exist_ok=True)
    _alerts().to_parquet(store)
    return p


def _state(p, gdf):
    state = AppState()
    state.new_project_state(p)
    state.alerts_gdf.set(gdf)
    return state


@solara.component
def _Harness(app_state, map_, layers):
    NotificationProvider()  # the tile's use_notifications() needs it rendered first
    overview_tile.OverviewTile(app_state=app_state, map_=map_, layers=layers)


@pytest.fixture
def ov(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))

    def render(state):
        map_ = SepalMap(gee=False)
        layers = MapLayers(map_)
        _, rc = reacton.render(_Harness(app_state=state, map_=map_, layers=layers),
                               handle_error=False)
        return map_, layers, rc

    return SimpleNamespace(render=render)


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def _texts(rc):
    return _strings(rc.container)


def _summary(rc):
    """The summary table as {label: count}."""
    [table] = rc.find(v.SimpleTable).widgets
    [tbody] = table.children
    return {str(row.children[0].children[0]): int(row.children[1].children[0]) for row in tbody.children}


def _export_cards(rc):
    return [w for w in rc.find(v.Card).widgets
            if any(isinstance(c, v.CardTitle) and c.children == [t("overview_tile.export_label")]
                   for c in w.children)]


# ---------------------------------------------------------------------------
# Summary, set label, stale banner and exports
# ---------------------------------------------------------------------------
def test_the_summary_counts_follow_the_alerts(ov, tmp_path):
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        counts = _summary(rc)
        assert counts == {
            t("overview_tile.total"): 4, t("overview_tile.reviewed"): 3,
            t("overview_tile.confirmed"): 1, t("overview_tile.false_positives"): 1,
            t("overview_tile.revision"): 1,
        }
        state.alerts_gdf.set(None)
        assert set(_summary(rc).values()) == {0}
    finally:
        rc.close()


def test_the_summary_rows_follow_a_language_switch(ov, tmp_path):
    """The rows are raw widgets rebuilt by an effect: a ``set_locale`` rebuilds
    them in the new language, counts unchanged, without new alerts."""
    state = _state(_project(tmp_path), _alerts())
    set_locale("en")
    map_, layers, rc = ov.render(state)
    try:
        english = _summary(rc)
        set_locale("fr")
        french = _summary(rc)
        assert french == {
            t("overview_tile.total"): 4, t("overview_tile.reviewed"): 3,
            t("overview_tile.confirmed"): 1, t("overview_tile.false_positives"): 1,
            t("overview_tile.revision"): 1,
        }
        assert set(french).isdisjoint(english)
    finally:
        set_locale("en")
        rc.close()


def test_the_export_card_follows_a_language_switch(ov, tmp_path):
    """The export card is a raw widget built once per set (its revealed
    download buttons must survive a re-render), so a ``set_locale`` relabels
    the same card instead of rebuilding it. The slice-6 walk found its title
    still English in the French Overview."""
    state = _state(_project(tmp_path), _alerts())
    set_locale("en")
    map_, layers, rc = ov.render(state)
    try:
        [card] = _export_cards(rc)
        set_locale("fr")
        cards = [w for w in rc.find(v.Card).widgets if w is card]
        assert cards == [card], "the card was rebuilt"
        assert t("overview_tile.export_label") == "Exporter les alertes"
        assert _export_cards(rc) == [card]
        download = f"{t('overview_tile.export_download')} {t('overview_tile.export_csv')}"
        assert download in _strings(card), _strings(card)
    finally:
        set_locale("en")
        rc.close()


def test_the_set_label_and_the_export_card_only_with_alerts(ov, tmp_path):
    p = _project(tmp_path)
    state = _state(p, _alerts())
    map_, layers, rc = ov.render(state)
    try:
        [select] = rc.find(v.Select).widgets
        assert select.v_model == p.active_set
        assert select.items == [{"text": "2026-01-01 → 2026-01-31 · GLAD-L · 4 alerts · 3 reviewed",
                                 "value": p.active_set}]
        assert len(_export_cards(rc)) == 1
        assert t("steps.overview.stale") not in _texts(rc)  # a current set: no banner

        state.alerts_gdf.set(_alerts().iloc[0:0])  # an empty set
        assert _export_cards(rc) == []
        state.alerts_gdf.set(None)
        assert _export_cards(rc) == []

        state.close_project_state()  # no project, no set
        assert _export_cards(rc) == []
        assert rc.find(v.Select).widgets == []
    finally:
        rc.close()


def test_the_export_card_writes_into_the_set_folder_named_by_sources_and_dates(ov, tmp_path, monkeypatch):
    calls = []
    real = overview_tile.build_export_card  # a real card: the tile relabels it on a language switch
    monkeypatch.setattr(overview_tile, "build_export_card", lambda *a: calls.append(a) or real(*a))
    alerts = _alerts()
    state = _state(_project(tmp_path), alerts)
    map_, layers, rc = ov.render(state)
    try:
        assert calls == [(alerts, str(tmp_path / "d" / SET_DIR), ["GLAD-L"], "2026-01-01", "2026-01-31", "d")]
        # a re-render with the same inputs keeps the card
        state.update_project("d", lambda p: setattr(p, "look_back_days", 30))
        assert len(calls) == 1
    finally:
        rc.close()


def test_an_out_of_date_set_shows_the_warning_banner(ov, tmp_path):
    state = _state(_project(tmp_path, current=False), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        assert t("steps.overview.stale") in _texts(rc)
        assert t("steps.overview.missing") not in _texts(rc)
    finally:
        rc.close()


def test_a_set_whose_files_are_missing_says_so(ov, tmp_path):
    """Review focus 4: the set's store was deleted or moved. The label still
    counts the manifest's alerts, so without this line the Overview would
    read "4 alerts" over an empty table with nothing to say why."""
    p = _project(tmp_path)
    (tmp_path / "d" / p.alert_sets[p.active_set].store_rel_path).unlink()
    state = _state(p, None)
    map_, layers, rc = ov.render(state)
    try:
        assert t("steps.overview.missing") in _texts(rc)
        assert t("steps.overview.stale") not in _texts(rc)
    finally:
        rc.close()
