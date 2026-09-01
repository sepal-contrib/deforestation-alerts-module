"""Overview export card: a click a user can actually make, and feedback.

The card is built by ``build_export_card`` — a module-level raw-widget builder —
so these tests drive the REAL buttons (the same objects the right panel renders)
rather than a handler pulled out of the component. That is the point: the
previous card wired the click to an ``sw.DownloadBtn``, whose ``path="#"``
default disables it in ``__init__``, so the export could never be triggered from
the UI at all even though a direct call to the handler worked.

The export worker runs on a real daemon thread (``spawn_in_context``); the
notification globals are replaced with recorders so what is asserted is the
message a real run publishes.
"""

import geopandas as gpd
import pytest
from pysepal import sepalwidgets as sw
from shapely.geometry import Polygon

import gui.tile.overview_tile as overview_tile
from deforestation_alerts.message import cm
from gui.scripts.solara_threads import spawn_in_context


def _gdf():
    g = gpd.GeoDataFrame(
        {"area_ha": [1.0], "alert_polygon": [None]},
        geometry=gpd.GeoSeries([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])], crs="EPSG:4326"),
    )
    g["bounding_box"] = g.geometry.envelope
    g["point"] = g.geometry.centroid
    return g


@pytest.fixture()
def feedback(monkeypatch):
    """Record the toasts/task events the export worker publishes, and hand back
    a joiner for the real worker threads."""
    events = []
    threads = []

    monkeypatch.setattr(
        overview_tile,
        "notify",
        lambda message, type_="info", **kw: events.append(("toast", type_, message)),
    )

    class _Task:
        def __init__(self, title, total_steps=None):
            events.append(("task:start", title, total_steps))

        def step(self, message):
            events.append(("task:step", message))

        def complete(self, message=None):
            events.append(("task:complete", message))

        def fail(self, message):
            events.append(("task:fail", message))

    monkeypatch.setattr(overview_tile, "track_task", _Task)

    def _spawn(target, args=(), **kwargs):
        thread = spawn_in_context(target, args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(overview_tile, "spawn_in_context", _spawn)

    def _join():
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "export worker did not finish"

    return events, _join


def _card(ws, folder, fmt_index=1):
    """The real export card plus the (generate, download) pair for one format."""
    card = overview_tile.build_export_card(
        _gdf(), ws, folder, ["GLAD-L"], "2026-01-01", "2026-06-12"
    )
    gen_row, dl_row = card.children[1], card.children[2]
    return card, gen_row.children[fmt_index], dl_row.children[fmt_index]


def test_the_generate_button_is_clickable_and_the_download_btn_starts_hidden(ws):
    """The bug: an ``sw.DownloadBtn`` built with the default ``path="#"`` is
    disabled by its own constructor, so nothing bound to its click ever runs."""
    card, gen_btn, dl_btn = _card(ws, ws.results_dir("recipe_a"))

    assert isinstance(gen_btn, sw.Btn) and not isinstance(gen_btn, sw.DownloadBtn)
    # (``v.Btn.disabled`` is None until set; what matters is that it is falsy —
    # the DownloadBtn below is explicitly True.)
    assert not gen_btn.disabled, "the generate button must be clickable"
    assert isinstance(dl_btn, sw.DownloadBtn)
    assert dl_btn.disabled is True and dl_btn.viz is False


def test_a_generated_export_enables_the_download_btn_with_a_home_relative_url(
    ws, feedback
):
    """The export lands in the user's workspace and the DownloadBtn becomes
    enabled + visible with a URL ``su.create_download_link`` can turn into a
    working SEPAL link. A container-absolute path would 404 silently, so the
    home-relative shape is the point of the assertion."""
    events, join = feedback
    folder = ws.results_dir("recipe_a")
    card, gen_btn, dl_btn = _card(ws, folder)

    gen_btn.fire_event("click", None)
    join()

    expected = f"{folder}/alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    assert ws.exists(expected)
    assert dl_btn.disabled is False and dl_btn.viz is True
    assert dl_btn.href.endswith(
        "module_results/deforestation_alerts/recipe_a/"
        "alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    )
    # the generate button is released, and the run reported success
    assert gen_btn.loading is False and gen_btn.disabled is False
    assert [e for e in events if e[0] == "toast"] == [
        ("toast", "success", str(cm.overview_tile.export_done).format(fmt="GPKG"))
    ]


def test_a_failed_export_tells_the_user_and_leaves_the_download_btn_disabled(
    ws, feedback, monkeypatch
):
    """An export that blows up used to be swallowed into the log: the user saw a
    dead button and no explanation."""
    events, join = feedback
    card, gen_btn, dl_btn = _card(ws, ws.results_dir("recipe_a"), fmt_index=0)

    def _boom(*_a, **_kw):
        raise ValueError("unsupported driver")

    monkeypatch.setattr(overview_tile, "export_alerts_gdf", _boom)

    gen_btn.fire_event("click", None)
    join()

    assert dl_btn.disabled is True and dl_btn.viz is False
    assert ws.client.files.files == {}
    kinds = [e[0] for e in events]
    assert "task:complete" not in kinds
    # fail() before the toast, so the run is logged as FAILED before the message
    assert kinds.index("task:fail") < kinds.index("toast")
    assert [e for e in events if e[0] == "toast"] == [
        ("toast", "error", "unsupported driver")
    ]
    assert gen_btn.loading is False and gen_btn.disabled is False
