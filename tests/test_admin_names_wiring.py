"""GAUL 2024 admin names wired in (officer critique 2026-11-11): Integrate
names every alert; the review Save names only a row that has none, from the
cached boundaries; a group keeps each row's own names and its record
shows their union."""

import geopandas as gpd
import pandas as pd
import pytest

from deforestation_alerts.engine import admin_names as an
from deforestation_alerts.engine import integrate
from deforestation_alerts.project import review_groups
from deforestation_alerts.project.models import AlertSetParams
from deforestation_alerts.project.paths import admin_boundaries_path
from gui.scripts.integrate_jobs import _done_message
from gui.tile.analysis import export, review_save
from gui.tile.analysis.state import capture_alert
from gui.i18n import t
from tests._review_helpers import boundaries
from tests.test_analysis_group_save import WIDGETS, _alerts, _analysis, _save, _setup, _stored
from tests.test_engine_integrate import _LONLAT_CENTROID, _job, JAN, ready_project  # noqa: F401 - fixture

pytestmark = pytest.mark.usefixtures("english_locale")


def _abc(pdir, aoi):
    return boundaries(("A", "B", "C"))


# ---- Integrate --------------------------------------------------------------
@_LONLAT_CENTROID
def test_integrate_names_every_alert(ready_project, monkeypatch):  # noqa: F811
    p, pdir = ready_project
    monkeypatch.setattr(integrate, "load_boundaries", _abc)
    result = integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1)))
    stored = gpd.read_parquet(pdir / result.alert_set.store_rel_path)
    assert len(stored) > 1 and not result.admin_names_failed
    assert set(stored["admin1"]) == {"A"} and set(stored["admin3"]) == {"C"}


@_LONLAT_CENTROID
def test_integrate_finishes_without_names_when_fetch_fails(ready_project, monkeypatch):  # noqa: F811
    p, pdir = ready_project

    def _fail(pdir, aoi):
        raise RuntimeError("no Earth Engine")

    monkeypatch.setattr(integrate, "load_boundaries", _fail)
    result = integrate.run_integration(_job(p, pdir, JAN, AlertSetParams(min_size_ha=0.1)))
    stored = gpd.read_parquet(pdir / result.alert_set.store_rel_path)
    assert len(stored) > 0 and result.admin_names_failed
    assert set(stored["admin1"]) == {""} and set(stored["admin3"]) == {""}
    assert t("steps.integrate.admin_names_failed") in _done_message(result)


# ---- the review Save --------------------------------------------------------
def _named(*names):
    gdf = _alerts()
    for i, n in enumerate(names):
        gdf.loc[i, ["admin1", "admin2", "admin3"]] = [n, n + "2", n + "3"]
    return gdf


def test_save_makes_no_lookup_when_rows_have_names(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, alerts=_named("X"))

    def _never(pdir, aoi):
        raise AssertionError("the Save fetched boundaries")

    monkeypatch.setattr(review_save, "load_boundaries", _never)
    wrote, _ = _save(h, 0)
    assert wrote is True and _stored(h)["admin1"].iloc[0] == "X"


def test_save_fills_missing_names(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch)
    wrote, _ = _save(h, 0)
    stored = _stored(h)
    assert wrote is True and h.lookups.calls == 1
    assert (stored["admin1"].iloc[0], stored["admin3"].iloc[0]) == ("Brasil", "Rio Branco")
    assert pd.isna(stored["admin1"].iloc[1])  # a row the Save does not write keeps none


def test_save_goes_on_when_boundaries_fail(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch)

    def _fail(pdir, aoi):
        raise RuntimeError("no Earth Engine")

    monkeypatch.setattr(review_save, "load_boundaries", _fail)
    wrote, _ = _save(h, 0)
    stored = _stored(h)
    assert wrote is True and stored["status"].iloc[0] == "Confirmed"
    assert not stored["admin1"].iloc[0] or pd.isna(stored["admin1"].iloc[0])


def test_save_keeps_stored_names(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, alerts=_named("Paraguay"))  # a GAUL 2015 review
    _save(h, 0)
    assert _stored(h)["admin1"].iloc[0] == "Paraguay" and h.lookups.calls == 0


def test_save_reads_the_cache_once_for_every_unnamed_row_of_a_group(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    _save(h, 0, {1, 2})
    assert h.lookups.calls == 1 and _stored(h)["admin1"].tolist()[:3] == ["Brasil"] * 3


def _cached_lookups(monkeypatch):
    """The real per-project cache in place of the stub, with a fetch that needs
    no Earth Engine; returns the project folders it was called with."""
    calls = []

    def load(pdir, aoi):
        calls.append(pdir)
        return an.load_boundaries(pdir, aoi, fetch=lambda bounds: boundaries(("Brasil", "Acre", "Rio Branco")))
    monkeypatch.setattr(review_save, "load_boundaries", load)
    return calls


def _save_with_steps(h, alert_id):
    steps = []
    h.s.actual_alert_id.set(alert_id)
    analysis = _analysis()
    alert = capture_alert(analysis, WIDGETS, h.s)
    wrote = export._save_attributes(WIDGETS, h.s, analysis, h.ctx, alert=alert,
                                    on_fetch=lambda: steps.append("fetch"))
    return wrote, steps


def test_the_first_fetch_shows_a_step_only_while_the_file_is_missing(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch)
    _cached_lookups(monkeypatch)
    assert _save_with_steps(h, 0) == (True, ["fetch"])
    assert _save_with_steps(h, 1) == (True, [])  # unnamed too, but the file is there now
    assert _stored(h)["admin1"].tolist()[:2] == ["Brasil", "Brasil"]


def test_a_review_copy_save_caches_the_boundaries_in_the_copy(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, copy_of="ana", alerts=_alerts(evaluator=["ana"] * 4))
    calls = _cached_lookups(monkeypatch)
    wrote, _ = _save(h, 0)
    pdir = h.s.project_dir
    assert wrote is True and calls == [pdir]
    assert _stored(h)["admin1"].iloc[0] == "Brasil"
    assert admin_boundaries_path(pdir, h.s.project.value.aoi.digest).is_file()


# ---- groups -----------------------------------------------------------------
def test_group_rows_keep_their_own_names(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True, alerts=_named("A", "B"))
    _save(h, 0, {1})
    stored = _stored(h)
    assert stored["admin1"].tolist()[:2] == ["A", "B"] and stored["admin3"].iloc[1] == "B3"


def test_group_record_unions_names():
    gdf = _alerts(3, admin1=["A", "B, A", "C"], admin2=["a", "", None])
    gdf[review_groups.COLUMN] = pd.array([10, 10, pd.NA], dtype="Int64")
    row, _ = review_groups.group_record(gdf, 1)
    assert row["admin1"] == "A, B" and row["admin2"] == "a"


def test_reset_keeps_names():
    gdf = _alerts(2, admin1=["A", "B"])
    assert "admin1" not in review_groups.reset_values(gdf, 0)

