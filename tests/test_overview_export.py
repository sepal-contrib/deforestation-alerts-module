"""Overview export card: a click a user can actually make, and feedback.

The card is built by ``build_export_card`` — a module-level raw-widget builder —
so these tests drive the REAL buttons (the same objects the right panel renders)
rather than a handler pulled out of the component. That is the point: the
previous card wired the click to an ``sw.DownloadBtn``, whose ``path="#"``
default disables it in ``__init__``, so the export could never be triggered from
the UI at all even though a direct call to the handler worked.

The export worker runs on a real daemon thread (``spawn_in_context``) under
``notify_bridge.tracked_job``; the module's ``current_notifier`` is pointed at a
real pysepal ``NotificationBus`` so what is asserted is the task + toasts a real
run publishes. Error toasts are counted on a ``Notifier.error`` spy because the
bus replaces earlier error toasts.
"""

from pathlib import Path

import geopandas as gpd
import pytest
from pysepal import sepalwidgets as sw
from pysepal.solara.notifications.bus import NotificationBus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import TaskStatus, ToastType
from shapely.geometry import Polygon

import gui.tile.overview_tile as overview_tile
from gui.scripts.notify_bridge import ERROR_TOAST_TIMEOUT
from gui.scripts.solara_threads import spawn_in_context
from gui.i18n import t


def _gdf():
    g = gpd.GeoDataFrame(
        {"area_ha": [1.0], "alert_polygon": [None]},
        geometry=gpd.GeoSeries([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])], crs="EPSG:4326"),
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

    monkeypatch.setattr(overview_tile, "current_notifier", lambda: Notifier(bus))

    real_error = Notifier.error

    def _spy_error(self, message, *, timeout=None):
        errors.append((message, timeout, tuple(t.status for t in bus.tasks.value)))
        real_error(self, message, timeout=timeout)

    monkeypatch.setattr(Notifier, "error", _spy_error)

    def _spawn(target, args=(), **kwargs):
        thread = spawn_in_context(target, args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(overview_tile, "spawn_in_context", _spawn)

    def _join():
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "export worker did not finish"

    return bus, errors, _join


def _non_error_toasts(bus):
    return [
        (t.type.value, t.message)
        for t in bus.toasts.value
        if t.type is not ToastType.ERROR
    ]


def _card(folder, fmt_index=1):
    """The real export card plus the (generate, download) pair for one format."""
    card = overview_tile.build_export_card(
        _gdf(), str(folder), ["GLAD-L"], "2026-01-01", "2026-06-12", "d"
    )
    gen_row, dl_row = card.children[1], card.children[2]
    return card, gen_row.children[fmt_index], dl_row.children[fmt_index]


def test_the_generate_button_is_clickable_and_the_download_btn_starts_hidden(data_root):
    """The bug: an ``sw.DownloadBtn`` built with the default ``path="#"`` is
    disabled by its own constructor, so nothing bound to its click ever runs."""
    card, gen_btn, dl_btn = _card(data_root / "recipe_a")

    assert isinstance(gen_btn, sw.Btn) and not isinstance(gen_btn, sw.DownloadBtn)
    # (``v.Btn.disabled`` is None until set; what matters is that it is falsy —
    # the DownloadBtn below is explicitly True.)
    assert not gen_btn.disabled, "the generate button must be clickable"
    assert isinstance(dl_btn, sw.DownloadBtn)
    assert dl_btn.disabled is True and dl_btn.viz is False


def test_a_generated_export_enables_the_download_btn_with_a_home_relative_url(
    data_root, feedback
):
    """The export lands in the set folder and the DownloadBtn becomes
    enabled + visible with a URL ``su.create_download_link`` can turn into a
    working SEPAL link: relative to ``$HOME``."""
    bus, errors, join = feedback
    folder = data_root / "recipe_a"
    card, gen_btn, dl_btn = _card(folder)

    gen_btn.fire_event("click", None)
    join()

    expected = f"{folder}/alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    assert Path(expected).exists()
    assert not list(folder.glob("*.part"))
    assert dl_btn.disabled is False and dl_btn.viz is True
    assert dl_btn.href.endswith(
        "module_results/deforestation_alerts/recipe_a/"
        "alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    )
    # the generate button is released, and the run reported success
    assert gen_btn.loading is False and gen_btn.disabled is False
    (task,) = bus.tasks.value
    assert task.status == TaskStatus.COMPLETED
    assert errors == []
    assert _non_error_toasts(bus) == [
        ("success", t("overview_tile.export_done", fmt="GPKG"))
    ]


def test_the_export_marks_its_project_as_being_written(data_root, feedback, monkeypatch):
    """Spec §12: the export writes into the set's folder, so the Project
    dialog's delete guard must see the project's name while it runs; without
    the mark a delete mid-export removed the folder and the export re-created
    it, a half-project with no manifest."""
    from gui.store.project_writers import is_writing

    bus, errors, join = feedback
    folder = data_root / "recipe_a"
    card, gen_btn, dl_btn = _card(folder)
    busy = []

    def _export(gdf, folder_, fmt, sources, start, end):
        busy.append(is_writing("d"))
        return f"{folder_}/alerts.{fmt}"

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _export)

    gen_btn.fire_event("click", None)
    join()

    assert busy == [True] and not is_writing("d")
    assert errors == [] and dl_btn.viz is True


def test_a_failed_export_tells_the_user_and_leaves_the_download_btn_disabled(
    data_root, feedback, monkeypatch, data_files
):
    """An export that blows up used to be swallowed into the log: the user saw a
    dead button and no explanation."""
    bus, errors, join = feedback
    card, gen_btn, dl_btn = _card(data_root / "recipe_a", fmt_index=0)

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _boom)

    gen_btn.fire_event("click", None)
    join()

    assert dl_btn.disabled is True and dl_btn.viz is False
    assert data_files(data_root) == []
    (task,) = bus.tasks.value
    assert task.status == TaskStatus.FAILED
    assert task.error_message == "unsupported driver"
    # exactly one error toast, published once the run was already FAILED
    assert errors == [
        ("unsupported driver", ERROR_TOAST_TIMEOUT, (TaskStatus.FAILED,))
    ]
    assert _non_error_toasts(bus) == []
    assert gen_btn.loading is False and gen_btn.disabled is False


def test_a_failed_export_leaves_one_traceback_in_the_server_log(
    data_root, feedback, monkeypatch, server_log
):
    """``tracked_job`` logs every failure it reports, with the traceback; the
    export worker logged the same failure again, so the server log showed
    two tracebacks for one failed export."""
    bus, errors, join = feedback
    card, gen_btn, dl_btn = _card(data_root / "recipe_a", fmt_index=0)

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _boom)

    gen_btn.fire_event("click", None)
    join()

    out = server_log.getvalue()
    assert out.count("Traceback (most recent call last)") == 1
    assert "failed: unsupported driver" in out and "ValueError: unsupported driver" in out
    assert len(errors) == 1 and gen_btn.loading is False
