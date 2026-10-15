"""Overview export card: scope chips, file tiles, a click a user can actually
make, and feedback (spec 2026-10-15 overview-export).

The card is built by ``build_export_card`` — a module-level raw-widget builder —
so these tests drive the REAL widgets (the same objects the dialog renders)
rather than a handler pulled out of the component: the tiles a user clicks and
the chips they pick a scope with.

The export worker runs on a real daemon thread (``spawn_in_context``) under
``notify_bridge.tracked_job``; the module's ``current_notifier`` is pointed at a
real pysepal ``NotificationBus`` so what is asserted is the task + toasts a real
run publishes. Error toasts are counted on a ``Notifier.error`` spy because the
bus replaces earlier error toasts.
"""

import os
import threading
import time

import geopandas as gpd
import pytest
from pysepal.i18n import set_locale
from pysepal.solara.notifications.bus import NotificationBus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import TaskStatus, ToastType
from shapely.geometry import Polygon

import gui.tile.overview_export as overview_export
from gui.i18n import t
from gui.scripts.notify_bridge import ERROR_TOAST_TIMEOUT
from gui.scripts.solara_threads import spawn_in_context

NAME = "alerts_glad-l_2026-01-01_2026-06-12"


def _gdf(*statuses):
    statuses = statuses or ("Confirmed",)
    polys = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(len(statuses))]
    g = gpd.GeoDataFrame(
        {"area_ha": [1.0] * len(statuses), "alert_polygon": [None] * len(statuses),
         "status": list(statuses)},
        geometry=gpd.GeoSeries(polys, crs="EPSG:4326"),
    )
    g["bounding_box"] = g.geometry.envelope
    g["point"] = g.geometry.representative_point()
    return g


@pytest.fixture()
def feedback(monkeypatch):
    """A real bus for the export worker, an error-toast spy, and a joiner for
    the real worker threads. Returns ``(bus, errors, join)``."""
    bus = NotificationBus()
    errors = []
    threads = []

    monkeypatch.setattr(overview_export, "current_notifier", lambda: Notifier(bus))

    real_error = Notifier.error

    def _spy_error(self, message, *, timeout=None):
        errors.append((message, timeout, tuple(t.status for t in bus.tasks.value)))
        real_error(self, message, timeout=timeout)

    monkeypatch.setattr(Notifier, "error", _spy_error)

    def _spawn(target, args=(), **kwargs):
        thread = spawn_in_context(target, args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(overview_export, "spawn_in_context", _spawn)

    def _join():
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "export worker did not finish"

    return bus, errors, _join


def _non_error_toasts(bus):
    return [(t.type.value, t.message) for t in bus.toasts.value if t.type is not ToastType.ERROR]


def _card(folder, gdf=None, store=None, questions=()):
    return overview_export.build_export_card(
        _gdf() if gdf is None else gdf, str(folder), ["GLAD-L"], "2026-01-01", "2026-06-12", "d",
        questions, store=store,
    )


def _text(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out += [c] if isinstance(c, str) else [_text(c)]
    return " ".join(out)


def _stale_setup(folder):
    """A confirmed-scope GeoPackage written a minute before the set's store."""
    folder.mkdir(parents=True, exist_ok=True)
    old = folder / f"{NAME}_confirmed.gpkg"
    old.write_bytes(b"x")
    store = folder / "alert_db.parquet"
    store.write_bytes(b"x")
    past = time.time() - 60
    os.utime(old, (past, past))
    return old, store


# ---------------------------------------------------------------------------
# What the card offers
# ---------------------------------------------------------------------------
def test_the_card_offers_two_formats_and_four_scopes(data_root):
    card = _card(data_root / "recipe_a", _gdf("Confirmed", "False Positive", "Not reviewed"))
    assert list(card.tiles) == ["gpkg", "csv"]
    assert list(card.chips) == ["confirmed", "reviewed", "unreviewed", "all"]
    assert card.scope == "confirmed"
    assert t("overview_tile.export_scope_reviewed") in _text(card.chips["reviewed"])
    assert "2" in _text(card.chips["reviewed"])
    for tile in card.tiles.values():  # the Create face: a clickable button, no link
        assert tile.path is None and not tile.disabled and tile.href is None
        assert "GeoJSON" not in _text(tile)


def test_the_summary_and_footer_follow_the_scope(data_root):
    card = _card(data_root / "recipe_a", _gdf("Confirmed", "Not reviewed"))
    assert t("overview_tile.export_summary", n="1", area="1.0", sources="GLAD-L",
             start="2026-01-01", end="2026-06-12") in _text(card)
    assert f"{NAME}_confirmed" in _text(card)
    card.scope_group.v_model = "all"
    assert t("overview_tile.export_summary", n="2", area="2.0", sources="GLAD-L",
             start="2026-01-01", end="2026-06-12") in _text(card)
    assert f"{NAME}_confirmed" not in _text(card) and NAME in _text(card)


def test_the_folder_chip_shows_the_served_path(data_root):
    card = _card(data_root / "recipe_a")
    assert card.folder_chip.text == "module_results/deforestation_alerts/recipe_a"


def test_the_default_scope_is_all_without_confirmed_alerts(data_root):
    card = _card(data_root / "recipe_a", _gdf("Not reviewed"))
    assert card.scope == "all"
    assert card.chips["confirmed"].disabled and card.chips["reviewed"].disabled
    assert not card.chips["unreviewed"].disabled and not card.chips["all"].disabled


# ---------------------------------------------------------------------------
# Writing a file
# ---------------------------------------------------------------------------
def test_a_click_writes_the_scoped_file_and_links_the_tile(data_root, feedback):
    bus, errors, join = feedback
    folder = data_root / "recipe_a"
    card = _card(folder, _gdf("Confirmed", "Not reviewed"))

    card.tiles["gpkg"].fire_event("click", None)
    join()

    path = folder / f"{NAME}_confirmed.gpkg"
    assert path.exists() and len(gpd.read_file(path)) == 1
    assert not list(folder.glob("*.part"))
    tile = card.tiles["gpkg"]
    assert tile.path == str(path) and tile.disabled is False and tile.warning is None
    assert tile.loading is False
    assert tile.href == (
        "/api/sandbox/jupyter/files/module_results/deforestation_alerts/recipe_a/"
        f"{NAME}_confirmed.gpkg"
    )
    (task,) = bus.tasks.value
    assert task.status == TaskStatus.COMPLETED and errors == []
    assert _non_error_toasts(bus) == [("success", t("overview_tile.export_done", fmt="GPKG"))]


def test_a_click_on_a_linked_tile_writes_nothing(data_root, feedback, monkeypatch):
    """A tile with a file is a download link: the browser follows it, and the
    click the kernel also hears must not start another write."""
    _, _, join = feedback
    folder = data_root / "recipe_a"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{NAME}_confirmed.gpkg").write_bytes(b"x")
    calls = []
    monkeypatch.setattr(overview_export, "export_alerts_gdf", lambda *a, **k: calls.append(a))
    card = _card(folder)
    card.tiles["gpkg"].fire_event("click", None)
    join()
    assert calls == []


def test_a_scope_switch_refaces_the_tiles(data_root, feedback):
    _, _, join = feedback
    folder = data_root / "recipe_a"
    card = _card(folder, _gdf("Confirmed", "Not reviewed"))
    card.tiles["csv"].fire_event("click", None)
    join()
    assert card.tiles["csv"].path == str(folder / f"{NAME}_confirmed.csv")

    card.scope_group.v_model = "all"
    assert card.tiles["csv"].path is None  # no all-alerts CSV yet
    card.scope_group.v_model = "confirmed"
    assert card.tiles["csv"].path == str(folder / f"{NAME}_confirmed.csv")


def test_an_existing_file_is_found_as_ready(data_root):
    """A file the previous card wrote (no scope suffix) is the "all" file."""
    folder = data_root / "recipe_a"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{NAME}.gpkg").write_bytes(b"x")
    card = _card(folder, _gdf("Not reviewed"))
    assert card.scope == "all"
    assert card.tiles["gpkg"].path == str(folder / f"{NAME}.gpkg")
    assert card.tiles["gpkg"].warning is None and card.update_btns["gpkg"].viz is False


def test_a_file_older_than_the_store_is_out_of_date(data_root, feedback):
    _, _, join = feedback
    folder = data_root / "recipe_a"
    old, store = _stale_setup(folder)
    card = _card(folder, store=store)
    tile = card.tiles["gpkg"]
    assert tile.path == str(old) and tile.warning == t("overview_tile.export_stale")
    assert card.update_btns["gpkg"].viz is True and card.update_btns["csv"].viz is False

    card.update_btns["gpkg"].fire_event("click", None)
    join()

    assert tile.path == str(old) and tile.warning is None
    assert card.update_btns["gpkg"].viz is False
    assert len(gpd.read_file(old)) == 1  # rewritten from the alerts


def test_a_failed_update_keeps_the_old_file(data_root, feedback, monkeypatch):
    _, errors, join = feedback
    folder = data_root / "recipe_a"
    old, store = _stale_setup(folder)
    card = _card(folder, store=store)

    def _boom(*_a, **_kw):
        raise ValueError("disk full")

    monkeypatch.setattr(overview_export, "export_alerts_gdf", _boom)
    card.update_btns["gpkg"].fire_event("click", None)
    join()

    tile = card.tiles["gpkg"]
    assert tile.path == str(old) and tile.warning == t("overview_tile.export_stale")
    assert card.update_btns["gpkg"].viz is True
    assert len(errors) == 1 and tile.loading is False


def test_scope_is_locked_while_writing(data_root, feedback, monkeypatch):
    _, _, join = feedback
    gate = threading.Event()
    seen = []

    def _slow(gdf, folder, fmt, sources, start, end, scope="all"):
        seen.append(scope)
        gate.wait(10)
        return f"{folder}/x.{fmt}"

    monkeypatch.setattr(overview_export, "export_alerts_gdf", _slow)
    card = _card(data_root / "recipe_a", _gdf("Confirmed", "Not reviewed"))
    card.tiles["gpkg"].fire_event("click", None)
    try:
        assert all(c.disabled for c in card.chips.values())
        assert card.tiles["gpkg"].loading is True
        card.scope_group.v_model = "all"  # a change that slips through is undone
        assert card.scope == "confirmed"
    finally:
        gate.set()
        join()
    assert seen == ["confirmed"]
    assert not card.chips["confirmed"].disabled and not card.chips["all"].disabled


def test_relabel_keeps_scope_and_files(data_root, feedback):
    _, _, join = feedback
    card = _card(data_root / "recipe_a", _gdf("Confirmed", "Not reviewed"))
    card.tiles["gpkg"].fire_event("click", None)
    join()
    set_locale("fr")
    try:
        same = overview_export.relabel_export_card(card)
        assert same is card
        assert t("overview_tile.export_label") == "Exporter les alertes"
        assert t("overview_tile.export_label") in _text(card)
        assert t("overview_tile.export_scope_all") in _text(card.chips["all"])
        assert card.scope == "confirmed" and card.tiles["gpkg"].path is not None
    finally:
        set_locale("en")


# ---------------------------------------------------------------------------
# What the file holds, and the project guard
# ---------------------------------------------------------------------------
def test_the_export_labels_current_questions_and_drops_deleted_ones(data_root, feedback):
    from deforestation_alerts.project.models import default_questions
    from gui.scripts import questions as questions_mod

    bus, errors, join = feedback
    folder = data_root / "recipe_a"
    gdf = _gdf()
    gdf["description"] = "logging, roads"
    gdf["q_gone"] = "x"
    card = _card(folder, gdf, questions=tuple(default_questions()))
    card.tiles["gpkg"].fire_event("click", None)
    join()

    out = gpd.read_file(folder / f"{NAME}_confirmed.gpkg")
    assert out["description"][0] == questions_mod.label(default_questions()[0], "logging, roads")
    assert "q_gone" not in out.columns
    assert errors == []


def test_the_export_marks_its_project_as_being_written(data_root, feedback, monkeypatch):
    """Spec §12: the export writes into the set's folder, so the Project
    dialog's delete guard must see the project's name while it runs; without
    the mark a delete mid-export removed the folder and the export re-created
    it, a half-project with no manifest."""
    from gui.store.project_writers import is_writing

    _, errors, join = feedback
    card = _card(data_root / "recipe_a")
    busy = []

    def _export(gdf, folder_, fmt, sources, start, end, scope="all"):
        busy.append(is_writing("d"))
        return f"{folder_}/alerts.{fmt}"

    monkeypatch.setattr(overview_export, "export_alerts_gdf", _export)
    card.tiles["gpkg"].fire_event("click", None)
    join()

    assert busy == [True] and not is_writing("d")
    assert errors == []


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------
def test_a_failed_export_tells_the_user_and_leaves_the_tile_to_retry(
    data_root, feedback, monkeypatch, data_files
):
    bus, errors, join = feedback
    card = _card(data_root / "recipe_a")

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_export, "export_alerts_gdf", _boom)
    card.tiles["csv"].fire_event("click", None)
    join()

    tile = card.tiles["csv"]
    assert tile.path is None and not tile.disabled and tile.loading is False
    assert not card.chips["confirmed"].disabled and not card.chips["all"].disabled  # unlocked again
    assert data_files(data_root) == []
    (task,) = bus.tasks.value
    assert task.status == TaskStatus.FAILED and task.error_message == "unsupported driver"
    assert errors == [("unsupported driver", ERROR_TOAST_TIMEOUT, (TaskStatus.FAILED,))]
    assert _non_error_toasts(bus) == []


def test_a_failed_export_leaves_one_traceback_in_the_server_log(
    data_root, feedback, monkeypatch, server_log
):
    _, errors, join = feedback
    card = _card(data_root / "recipe_a")

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_export, "export_alerts_gdf", _boom)
    card.tiles["csv"].fire_event("click", None)
    join()

    out = server_log.getvalue()
    assert out.count("Traceback (most recent call last)") == 1
    assert "failed: unsupported driver" in out and "ValueError: unsupported driver" in out
    assert len(errors) == 1


def test_the_download_url_follows_a_configured_base(data_root, feedback, monkeypatch):
    """A local jupyter serves its root at ``/files``."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DOWNLOAD_BASE", "/files")
    monkeypatch.setenv("DEFORESTATION_ALERTS_DOWNLOAD_ROOT", str(data_root))
    _, _, join = feedback
    card = _card(data_root / "recipe_a")
    card.tiles["gpkg"].fire_event("click", None)
    join()
    assert card.tiles["gpkg"].href == f"/files/recipe_a/{NAME}_confirmed.gpkg"


def test_a_folder_outside_the_download_root_still_builds_and_says_why(data_root, feedback, monkeypatch):
    """A set folder the server cannot offer for download (a data dir outside
    the download root) must not break the Overview: the folder chip shows the
    absolute path, a file already there is not linked (the link would 404),
    and a new write is reported as a failure that says why."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DOWNLOAD_ROOT", str(data_root / "elsewhere"))
    bus, errors, join = feedback
    folder = data_root / "recipe_a"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{NAME}_confirmed.csv").write_bytes(b"x")

    card = _card(folder)

    assert card.folder_chip.text == str(folder)
    csv = card.tiles["csv"]
    assert csv.path is None and csv.disabled is True
    assert "elsewhere" in _text(csv)  # the reason, in the tile

    card.tiles["gpkg"].fire_event("click", None)
    join()
    (task,) = bus.tasks.value
    assert task.status == TaskStatus.FAILED and len(errors) == 1
    assert card.tiles["gpkg"].path is None and card.tiles["gpkg"].loading is False


def test_the_summary_area_keeps_one_decimal(data_root):
    """A set of small confirmed alerts must not read "0 ha"."""
    gdf = _gdf("Confirmed")
    gdf["area_ha"] = [0.4]
    card = _card(data_root / "recipe_a", gdf)
    assert t("overview_tile.export_summary", n="1", area="0.4", sources="GLAD-L",
             start="2026-01-01", end="2026-06-12") in _text(card)


def test_a_rebuilt_card_keeps_the_scope_it_is_given(data_root):
    """The Overview rebuilds the card after a review save and hands it the
    scope the user had picked; a scope with no alerts falls back to the default."""
    folder = data_root / "recipe_a"
    gdf = _gdf("Confirmed", "Not reviewed")
    card = overview_export.build_export_card(gdf, str(folder), ["GLAD-L"], "2026-01-01", "2026-06-12",
                                             "d", scope="unreviewed")
    assert card.scope == "unreviewed"
    card = overview_export.build_export_card(_gdf("Confirmed"), str(folder), ["GLAD-L"], "2026-01-01",
                                             "2026-06-12", "d", scope="unreviewed")
    assert card.scope == "confirmed"
