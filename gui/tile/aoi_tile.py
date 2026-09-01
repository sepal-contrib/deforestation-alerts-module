"""AOI/Date tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara + ipyvuetify/pysepal for UI only. The
GEE-heavy search runs as an **async** ``solara.lab.use_task(prefer_threaded=False)``
on Solara's kernel loop, with every blocking eeclient call inside
``asyncio.to_thread``; the model mutations and the ccdc dictionary observer stay
on the (service) helper functions.

Task lifecycle (the four-part pattern used for every long-running action here —
the alerts-dictionary init, the search, and the AOI map layer):

1. a **pure sync worker** takes a frozen request snapshot and returns a frozen
   outcome — no model / reactive / widget mutation, no navigation;
2. an **async task** on the kernel loop (``prefer_threaded=False``) awaits that
   worker through ``asyncio.to_thread`` (this is also where ``add_ee_layer``
   runs — never from render or an effect body);
3. the **click handler** takes the snapshot and holds a synchronous ``pending``
   guard (a render-time ``disabled=`` cannot stop a double click, because the
   click that follows the first one is dispatched against the pre-render button);
4. a **completion effect** with the full dependency list
   ``[pending, finished, error, cancelled]`` owns every model write, the
   ``current_page_view`` navigation and the success/error **toast**, and applies
   nothing when the task was cancelled.

User feedback is published through the pysepal notification system
(``use_notifications()`` — the hook, because this module is a Solara component),
never rendered inline: there is no ``solara.Error``/``solara.Info`` status
element in this tile.

Key port decisions (see Phase 4 plan, Step 2):

* **AoiView ``model=`` is the recipe ``aoi_date_model``.** ``AoiDateModel`` extends
  ``AoiModel``, so passing it straight to ``AoiView(model=...)`` makes AOI
  selection write directly into the recipe model (the view calls
  ``model.set_object()`` → ``feature_collection``). This removes the OLD tile's
  "copy aoi_view.model fields → aoi_date_model" boilerplate and the redundant
  ``_from_*`` re-dispatch in ``process_alerts``.
* **The 1 traitlets observer is kept as a traitlets observer.** ``aux_model``'s
  ``ccdc_layer`` change mutates ``alert_filter_model.alerts_dictionary``
  (model→model, no Solara UI dependency); a Solara ``use_effect`` keyed on the
  trait would not fire (traitlets changes don't trigger Solara re-renders), so we
  register a real ``aux_model.observe`` exactly once via ``use_effect([])`` and
  tear it down on unmount.
* **Search is a button-triggered task, not auto-run.** ``use_task(dependencies=None)``
  only runs when called, so it never fires on mount (which would hit EE with no
  inputs); the click handler snapshots the inputs and calls
  ``search_task(SearchRequest(...))``, and the button is the pysepal
  ``TaskButtonComponent`` (single start/cancel toggle — never a second button).
* **AoiView + DatePickers are memoized once** (``dependencies=[]``) so the widget
  identities and the ``.bind()`` chain are stable across re-renders. The AoiView
  is attached to the **single shared map** (``map_``) passed in from
  ``solara_app.Page`` — this tile does NOT create its own map (pysepal single-map
  model). AOI selection draws on the shared map; the tile renders only its
  controls (a right-panel section).
"""

import asyncio
import dataclasses
import logging
import time
from datetime import date
from types import SimpleNamespace
from typing import Any

import ipyvuetify as v
import solara
from pysepal import aoi
from pysepal import sepalwidgets as sw
from pysepal.solara.components.task_button import TaskButtonComponent, use_task_button
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.aoi_helper import (
    add_custom_alerts_dictionary,
    check_aoi_inputs,
    create_available_alert_dictionary,
    create_basic_alerts_dictionary,
    create_filtered_alert_raster_dictionary,
    get_alert_dates_ccdc,
    remove_ccdc_alerts_dictionary,
    remove_custom_alerts_dictionary,
)
from gui.scripts.solara_threads import spawn_in_context

logger = logging.getLogger("SEPALUI.deforestation_alerts.aoi")

# A click on the pysepal TaskButton toggles start -> cancel, and cancel is never
# disabled (by design). A fast double click on "Search" can therefore land its
# second press on the already-toggled cancel state and kill the run the user just
# started. Cancels arriving within this grace window of the start are treated as
# that accidental second press and ignored; a deliberate cancel always comes
# later than this.
_CANCEL_GRACE_SECONDS = 1.0


class _InputsShim:
    """Adapter exposing the attribute shape ``check_aoi_inputs`` expects.

    The service helper ``check_aoi_inputs(self)`` reads
    ``self.aoi_view.model.feature_collection`` and ``self.start_date.v_model`` /
    ``self.end_date.v_model`` (it was written against the OLD widget-class
    ``self``). Rather than fork the still-shared helper, we hand it this thin
    shim built from the immutable ``SearchRequest`` snapshot, so the validation
    logic stays in one place AND the worker never touches a live widget.
    """

    def __init__(self, feature_collection, start_date, end_date):
        self.aoi_view = SimpleNamespace(
            model=SimpleNamespace(feature_collection=feature_collection)
        )
        self.start_date = SimpleNamespace(v_model=start_date)
        self.end_date = SimpleNamespace(v_model=end_date)


@dataclasses.dataclass(frozen=True)
class SearchRequest:
    """Immutable snapshot of everything the search needs, taken at click time."""

    feature_collection: Any
    start_date: Any
    end_date: Any
    alerts_dictionary: Any
    ccdc_layer: Any


@dataclasses.dataclass(frozen=True)
class SearchOutcome:
    """What the search produced. Applied by the completion effect, never here."""

    available_alerts_list: list
    available_alerts_raster_list: dict
    alerts_dictionary: dict
    dictionary_rebuilt: bool = False


@dataclasses.dataclass(frozen=True)
class AoiLayerRequest:
    """Immutable snapshot for one AOI map-layer sync (draw when ``show``)."""

    show: bool
    token: int


@dataclasses.dataclass(frozen=True)
class CcdcUpdateRequest:
    """Immutable snapshot for one CCDC-dictionary sync (token-guarded).

    ``ccdc_layer`` is captured at trigger time (inside the traitlets handler, on
    the main/render thread) rather than re-read live inside the worker thread —
    the same "capture now, check token before the write" shape as
    ``AoiLayerRequest``/``_apply_aoi_layer`` below.
    """

    ccdc_layer: Any
    token: int


def _run_search_sync(request: SearchRequest) -> SearchOutcome:
    """Pure search worker — runs inside ``asyncio.to_thread``.

    Every call below is the blocking eeclient API (``create_*`` reach
    ``.getInfo()``), which is exactly why this whole function is confined to a
    thread. It mutates nothing: the dictionaries/lists it builds travel back in a
    ``SearchOutcome`` and the component's completion effect writes them onto the
    models.
    """
    feature_collection, date1, date2 = check_aoi_inputs(
        _InputsShim(request.feature_collection, request.start_date, request.end_date)
    )

    dictionary = request.alerts_dictionary
    dictionary_rebuilt = False
    if not isinstance(dictionary, dict):
        # Defensive: the init task has not landed yet (or it failed). Rebuild
        # here — still on the worker thread — and hand it back for the effect.
        dictionary = create_basic_alerts_dictionary()
        dictionary_rebuilt = True

    available = create_available_alert_dictionary(
        dictionary, feature_collection, date1, date2
    )
    rasters = create_filtered_alert_raster_dictionary(
        available,
        feature_collection,
        date1,
        date2,
        request.ccdc_layer,
        full_dictionary=dictionary,
    )
    return SearchOutcome(
        available_alerts_list=available,
        available_alerts_raster_list=rasters,
        alerts_dictionary=dictionary,
        dictionary_rebuilt=dictionary_rebuilt,
    )


def _update_dictionary_ccdc(alert_filter_model, request: CcdcUpdateRequest, generation):
    """Add/remove the CCDC entry in ``alert_filter_model.alerts_dictionary``.

    Same logic as the OLD tile's ``update_dictionary_ccdc`` method, lifted to a
    module function. The add branch makes two blocking ``ee.Date(...).getInfo()``
    round-trips (``get_alert_dates_ccdc``) — Decision 3 confines that to a thread,
    so the caller MUST invoke this off the render/observer thread (see
    ``_register_ccdc_observer``'s ``spawn_in_context`` wrapper), never call it
    directly from a trait observer.

    Ordering race + fix, same idiom as ``AoiLayerRequest``/``_apply_aoi_layer``
    below: picking asset A then quickly correcting to B starts two independent
    round-trips (two observer fires -> two threads). If A's round-trip is
    slower than B's, it would otherwise finish last and silently overwrite B's
    correct entry with A's stale one. ``request.token`` is the
    ``generation["value"]`` the caller bumped to at trigger time; the actual
    dictionary write (add or remove) only happens if ``generation["value"]``
    still equals ``request.token`` once the round-trip returns — a superseded
    call is a no-op instead of a stale overwrite. Also guarded so a missing
    ``CCDC`` key (double-clear) does not raise.
    """
    dictionary = alert_filter_model.alerts_dictionary
    if not isinstance(dictionary, dict):
        # alerts_dictionary not initialized yet (e.g. EE init failed) — nothing
        # to mutate; surface in logs rather than crash the observer thread.
        logger.warning("ccdc observer fired before alerts_dictionary was a dict")
        return
    try:
        if request.ccdc_layer:
            dates_ccdc = get_alert_dates_ccdc(request.ccdc_layer)
            if generation["value"] != request.token:
                return  # superseded by a later ccdc change; discard this stale result
            dictionary["CCDC"] = {
                "collection": request.ccdc_layer,
                "start_date": dates_ccdc[0],
                "end_date": dates_ccdc[1],
            }
        elif request.ccdc_layer is None and "CCDC" in dictionary:
            if generation["value"] != request.token:
                return
            remove_ccdc_alerts_dictionary(dictionary)
    except Exception:
        logger.exception("Failed to update CCDC entry in alerts_dictionary")


def _update_dictionary_custom(aux_model, alert_filter_model):
    """Add/remove the user-defined custom alert entry in ``alerts_dictionary``.

    Mirrors ``_update_dictionary_ccdc`` for the custom (RADD-shaped) source. Both a
    name AND an asset are required to register it (a partial config registers
    nothing); clearing either removes it. The entry uses a wide date range so the
    availability check passes — the real date filtering happens in ``_from_custom``.
    """
    dictionary = alert_filter_model.alerts_dictionary
    if not isinstance(dictionary, dict):
        logger.warning("custom observer fired before alerts_dictionary was a dict")
        return
    try:
        name = aux_model.custom_alert_name
        asset = aux_model.custom_alert_layer
        if name and asset:
            add_custom_alerts_dictionary(
                dictionary, name, asset, "2015-01-01", date.today().strftime("%Y-%m-%d")
            )
        else:
            remove_custom_alerts_dictionary(dictionary)
    except Exception:
        logger.exception("Failed to update custom entry in alerts_dictionary")


@solara.component
def AoiTile(
    aoi_date_model, alert_filter_model, aux_model, app_state, gee_interface, map_
):
    """Solara port of the old AoiTile ipyvuetify widget.

    Parameters
    ----------
    aoi_date_model : AoiDateModel
        Recipe domain model (AOI fields + start_date/end_date). Passed straight
        to the AoiView as its ``model=`` so AOI selection writes into the recipe.
    alert_filter_model : AlertFilterModel
        Domain model holding ``alerts_dictionary`` / ``available_alerts_list`` /
        ``available_alerts_raster_list`` populated by the search.
    aux_model : AuxModel
        Auxiliary model; its ``ccdc_layer`` trait drives the CCDC dictionary entry.
    app_state : AppState
        Shared ephemeral reactive store (used for post-search navigation intent).
    gee_interface : GEEInterface
        Session GEE interface, forwarded to the AoiView.
    map_ : SepalMap
        The single shared map; the AoiView draws AOI selection on it.
    """
    # This is a Solara component, so user feedback goes through the HOOK
    # (``use_notifications()``), never the ``notify()`` global: the hook binds
    # the notifier to the bus of the kernel that is rendering, which is the only
    # binding that is correct for per-session UI. ``NotificationProvider`` is
    # mounted above this component in ``solara_app.Page``, so this resolves to a
    # real Notifier (a NoopNotifier only when rendered without the provider,
    # e.g. in a unit test).
    notifications = use_notifications()

    # ------------------------------------------------------------------
    # Initialize the alerts dictionary + available lists exactly once.
    # ``create_basic_alerts_dictionary`` makes three blocking ``.getInfo()``
    # round-trips (GLAD-L / GLAD-S2 / RADD collection dates), so it must NOT run
    # at render time — it used to sit in a ``use_memo``, which blocked the first
    # render of the whole page on the network. It is now an async task on the
    # kernel loop whose eeclient work happens in a thread, fired once from a
    # mount effect; the completion effect writes the model fields the render
    # reads. If EE is unavailable we fall back to {} and log — the tile still
    # renders.
    # ------------------------------------------------------------------
    async def _init_alerts_dictionary():
        return await asyncio.to_thread(create_basic_alerts_dictionary)

    init_task = solara.lab.use_task(
        _init_alerts_dictionary,
        dependencies=None,
        prefer_threaded=False,
        raise_error=False,
    )

    def _start_init():
        if not init_task.not_called:
            return
        if _loop_is_running():
            init_task()
        else:
            # Same exposure as the layer tasks (see _loop_is_running), but the
            # fallback must NOT be "do it inline": that is exactly the render-time
            # .getInfo() this task exists to remove. Skip instead — the search
            # worker rebuilds the dictionary on its own thread when it finds the
            # model field unset (SearchOutcome.dictionary_rebuilt).
            logger.warning(
                "No running event loop at mount; deferring the alerts-dictionary "
                "init to the first search"
            )

    solara.use_effect(_start_init, dependencies=[])

    def _apply_init():
        if init_task.cancelled or init_task.pending:
            return
        if init_task.finished and init_task.value is not None:
            alert_filter_model.alerts_dictionary = init_task.value
        elif init_task.error:
            logger.error(
                "Failed to build basic alerts dictionary (EE down?)",
                exc_info=init_task.exception,
            )
            alert_filter_model.alerts_dictionary = {}
        else:
            return
        alert_filter_model.available_alerts_list = []
        alert_filter_model.available_alerts_raster_list = []

    solara.use_effect(
        _apply_init,
        [init_task.pending, init_task.finished, init_task.error, init_task.cancelled],
    )

    # ------------------------------------------------------------------
    # Memoized AoiView, attached to the single shared map. AoiView's model IS
    # the recipe model, so selection writes straight into aoi_date_model and the
    # AOI is drawn on the shared map.
    # ------------------------------------------------------------------
    aoi_view = solara.use_memo(
        lambda: _build_aoi_view(aoi_date_model, map_, gee_interface),
        dependencies=[],
    )

    # ------------------------------------------------------------------
    # Memoized date pickers, bound once to the recipe model traits.
    # ------------------------------------------------------------------
    date_pickers = solara.use_memo(
        lambda: _build_date_pickers(aoi_date_model), dependencies=[]
    )
    start_picker, end_picker = date_pickers

    # ------------------------------------------------------------------
    # ccdc traitlets observer — registered exactly once, torn down on unmount.
    # (A Solara use_effect keyed on aux_model.ccdc_layer would NOT fire: traitlets
    # mutations don't trigger Solara re-renders.)
    #
    # The handler itself must stay non-blocking: _update_dictionary_ccdc's add
    # branch makes two blocking GEE .getInfo() calls (get_alert_dates_ccdc), and
    # traitlets fires observers synchronously on the thread that set the trait
    # (here, the render/kernel thread). Calling it inline would block the UI on
    # network I/O — exactly what Decision 3 (blocking GEE confined to a thread)
    # forbids. spawn_in_context defers the dictionary mutation to a daemon
    # thread instead (same pattern as the Analysis tile's button handlers).
    #
    # ccdc_generation is the same superseded-write guard as layer_generation /
    # _apply_aoi_layer further below: the handler bumps the counter and captures
    # the ccdc_layer value + the bumped token into an immutable CcdcUpdateRequest
    # right here (on the render thread, before any network work), then
    # _update_dictionary_ccdc checks the token immediately before it writes.
    # Without this, picking asset A then quickly correcting to B starts two
    # independent round-trips; if A's is slower it would land after B's and
    # silently overwrite B's correct entry with A's stale one.
    # ------------------------------------------------------------------
    ccdc_generation = solara.use_memo(lambda: {"value": 0}, dependencies=[])

    def _register_ccdc_observer():
        def handler(change):
            ccdc_generation["value"] += 1
            request = CcdcUpdateRequest(
                ccdc_layer=aux_model.ccdc_layer, token=ccdc_generation["value"]
            )
            spawn_in_context(
                _update_dictionary_ccdc,
                (alert_filter_model, request, ccdc_generation),
            )

        aux_model.observe(handler, "ccdc_layer")
        return lambda: aux_model.unobserve(handler, "ccdc_layer")

    solara.use_effect(_register_ccdc_observer, dependencies=[])

    # ------------------------------------------------------------------
    # custom-alert traitlets observer — same pattern as ccdc, observing both the
    # asset and the name (either change re-syncs the single custom dictionary entry).
    # ------------------------------------------------------------------
    def _register_custom_observer():
        def handler(change):
            _update_dictionary_custom(aux_model, alert_filter_model)

        aux_model.observe(handler, ["custom_alert_layer", "custom_alert_name"])
        return lambda: aux_model.unobserve(
            handler, ["custom_alert_layer", "custom_alert_name"]
        )

    solara.use_effect(_register_custom_observer, dependencies=[])

    # ------------------------------------------------------------------
    # Single shared map -> per-tile isolation. Show the AOI outline only while
    # this tile is the active map tile; clear it when navigating to another
    # right-panel tile so it doesn't persist over Filter / Overview. Keyed on
    # active_map_view (NOT current_page_view) so opening a popup over the map
    # leaves the outline in place; returning re-adds the layer from the model.
    # Selection while already active is handled by AoiView itself (active doesn't
    # change).
    # ------------------------------------------------------------------
    # ``_redraw_aoi_layer`` calls ``map_.add_ee_layer``, which blocks on EE while it
    # resolves the tile-map id — so it must not run in the effect body itself.
    # The effect only schedules the async layer task, which does the map work in
    # ``asyncio.to_thread`` (the pysepal-verified placement for ``add_ee_layer``).
    #
    # ``layer_generation`` makes a superseded draw a no-op: cancelling the task
    # abandons the coroutine but the thread it started keeps running, so a slow
    # "draw" from a previous activation could otherwise re-add the outline after
    # a later "clear". Each effect run bumps the counter and passes it as the
    # request token; the thread checks the token immediately before it touches
    # the map and returns when it has been superseded.
    active = app_state.active_map_view.value == "aoi_tile"
    layer_generation = solara.use_memo(lambda: {"value": 0}, dependencies=[])

    async def _sync_aoi_layer(request: AoiLayerRequest):
        await asyncio.to_thread(
            _apply_aoi_layer, aoi_view, map_, request, layer_generation
        )
        return request

    layer_task = solara.lab.use_task(
        _sync_aoi_layer, dependencies=None, prefer_threaded=False, raise_error=False
    )

    def _sync_active():
        layer_generation["value"] += 1
        request = AoiLayerRequest(show=active, token=layer_generation["value"])
        if _loop_is_running():
            layer_task(request)
        else:
            # No running loop on this thread, so solara's bare
            # ``asyncio.create_task`` inside a prefer_threaded=False task would
            # raise and take the whole render down with it (reacton then masks it
            # behind "you are not using a container"). Effects must never raise:
            # fall back to the pre-refactor behaviour and do the map work inline
            # — inside the same try/except every other inline map call in this
            # module uses, since `_redraw_aoi_layer` reaches EE via
            # `add_ee_layer` and an EE/map failure here would raise out of the
            # effect, which is the very thing the guard exists to prevent.
            try:
                _apply_aoi_layer(aoi_view, map_, request, layer_generation)
            except Exception:
                logger.exception("AOI: inline AOI-layer sync failed")

    solara.use_effect(_sync_active, dependencies=[active])

    # ------------------------------------------------------------------
    # Search task. dependencies=None => never auto-runs; only fires when the
    # click handler calls search_task(request). The coroutine stays on the kernel
    # loop and hands the whole blocking eeclient body to a thread; NOTHING is
    # written to a model here — see _apply_search below.
    # ------------------------------------------------------------------
    async def _search(request: SearchRequest) -> SearchOutcome:
        return await asyncio.to_thread(_run_search_sync, request)

    search_task = solara.lab.use_task(
        _search, dependencies=None, prefer_threaded=False, raise_error=False
    )
    search_started_at = solara.use_ref(0.0)
    search_cancel_reason = solara.use_ref(None)

    def _start_search():
        # Synchronous pending guard: a render-time ``disabled=`` cannot stop the
        # second press of a double click (it is dispatched against the button as
        # it was before the re-render), but this can.
        if search_task.pending:
            return
        # AOI fields are already on aoi_date_model (AoiView wrote them); dates are
        # already bound from the pickers. Snapshot them here, at click time, so
        # the worker can never observe a half-changed form.
        search_cancel_reason.current = None
        search_started_at.current = time.monotonic()
        logger.info("AOI: alert search started")
        search_task(
            SearchRequest(
                feature_collection=getattr(
                    aoi_view.model, "feature_collection", None
                ),
                start_date=start_picker.v_model,
                end_date=end_picker.v_model,
                alerts_dictionary=alert_filter_model.alerts_dictionary,
                ccdc_layer=aux_model.ccdc_layer,
            )
        )

    def _apply_search():
        # The ONLY place search results reach the models. A cancelled run leaves
        # every model and the current page exactly as they were.
        if search_task.cancelled:
            logger.info("AOI: alert search cancelled — models and page unchanged")
            return
        if search_task.error:
            # ``search_task.exception`` is the raised exception; ``search_task.error``
            # is a bool, so str()-ing it would publish "True".
            logger.error("AOI: alert search failed", exc_info=search_task.exception)
            notifications.error(str(search_task.exception))
            return
        if not search_task.finished:
            return
        outcome = search_task.value
        if outcome is None:
            return
        if outcome.dictionary_rebuilt:
            alert_filter_model.alerts_dictionary = outcome.alerts_dictionary
        alert_filter_model.available_alerts_list = outcome.available_alerts_list
        alert_filter_model.available_alerts_raster_list = (
            outcome.available_alerts_raster_list
        )
        logger.info(
            "AOI: alert search finished — %s source(s) available",
            len(outcome.available_alerts_list),
        )
        notifications.success(
            cm.aoi_tile.search_done.format(n=len(outcome.available_alerts_list))
        )
        # Jump to the Filter tile (as the OLD tile did). The shell's _NavBridge
        # translates this current_page_view write into the MapApp step change.
        app_state.current_page_view.value = "filter_alerts"

    solara.use_effect(
        _apply_search,
        [
            search_task.pending,
            search_task.finished,
            search_task.error,
            search_task.cancelled,
        ],
    )

    search_button_props = use_task_button(
        search_task, on_start=_start_search, cancel_reason_ref=search_cancel_reason
    )
    search_button_props["on_cancel"] = _guard_cancel(
        search_button_props["on_cancel"], search_started_at
    )

    # ------------------------------------------------------------------
    # Render layout: controls only (this widget IS the right-panel section
    # content). AOI selection is drawn on the single shared map.
    # ------------------------------------------------------------------
    aoi_card = v.Card(
        class_="pa-3 ma-3",
        hover=True,
        dense=True,
        children=[
            v.CardTitle(class_="pa-1 ma-1", children=[cm.aoi_tile.aoi_selection_title]),
            aoi_view,
        ],
    )
    date_card = v.Card(
        class_="pa-3 ma-3",
        hover=True,
        dense=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.aoi_tile.date_selection_title]
            ),
            start_picker,
            end_picker,
        ],
    )

    with solara.Column(style="padding: 0;"):
        solara.display(aoi_card)
        solara.display(date_card)
        with solara.Div(style="padding: 12px;"):
            TaskButtonComponent(
                label=cm.aoi_tile.search_button,
                **search_button_props,
                block=True,
            )
        # No inline feedback here: search failures are published as an error
        # toast from ``_apply_search`` (the notification system is the app's
        # single user-feedback channel).


def _loop_is_running() -> bool:
    """True when the calling thread has a running asyncio event loop.

    ``solara.lab.use_task(prefer_threaded=False)`` schedules with a bare
    ``asyncio.create_task``, which needs one; click handlers and ordinary
    re-renders have it, but a render reached from a thread that does not (e.g.
    the detached ``render_fixed`` mount in ``solara_app._render_detached``) would
    get ``RuntimeError: no running event loop``. Callers scheduling a task from
    an effect body check this first and fall back to doing the work inline.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _guard_cancel(on_cancel, started_at):
    """Wrap a TaskButton cancel callback so an accidental double click can't cancel.

    The pysepal TaskButton is a single start/cancel toggle and cancel is never
    disabled, so the second press of a fast double click on "Search"/"Analyze"
    can arrive after the server already re-rendered the button into its cancel
    state and kill the run that the first press just started. Cancels within
    ``_CANCEL_GRACE_SECONDS`` of the start are dropped; a deliberate cancel is
    always later than that. Returning early also leaves ``cancel_reason_ref``
    untouched (the wrapped callback is what sets it).
    """

    def _on_cancel(*args, **kwargs):
        if time.monotonic() - (started_at.current or 0.0) < _CANCEL_GRACE_SECONDS:
            return
        on_cancel(*args, **kwargs)

    return _on_cancel


def _apply_aoi_layer(aoi_view, map_widget, request: AoiLayerRequest, generation):
    """Draw or clear the AOI layer on the shared map — runs in ``to_thread``.

    ``generation["value"]`` is bumped by the scheduling effect; a run whose token
    no longer matches has been superseded (the task was cancelled/replaced but
    its thread survives cancellation) and must not touch the map.
    """
    if generation["value"] != request.token:
        return
    if request.show:
        _redraw_aoi_layer(aoi_view, map_widget)
    else:
        _clear_aoi_layer(map_widget)


def _clear_aoi_layer(map_widget):
    """Remove only the AoiView-managed AOI layer (named ``"aoi"``) from the shared
    map. Single-map isolation: called when the AOI tile is deactivated so the AOI
    outline doesn't persist over Filter / Overview. Never ``remove_all()``.
    """
    map_widget.remove_layer("aoi", none_ok=True)


def _redraw_aoi_layer(aoi_view, map_widget):
    """Re-add the AOI layer from the current model when returning to the AOI tile.

    Mirrors the map step of ``AoiView._update_aoi`` (aoi_view.py:380-386) — re-add
    by name from ``model.feature_collection`` — WITHOUT re-running selection or
    re-zooming. No-op when no AOI is selected. Couples to the stable AoiView layer
    name (``"aoi"``) + ``.gee`` / ``.map_style`` attributes.
    """
    map_widget.remove_layer("aoi", none_ok=True)
    model = aoi_view.model
    if getattr(model, "feature_collection", None) is None:
        return
    if aoi_view.gee:
        map_widget.add_ee_layer(model.feature_collection, {}, "aoi")
    else:
        map_widget.add_layer(model.get_ipygeojson(aoi_view.map_style), "aoi")


def _build_aoi_view(aoi_date_model, map_widget, gee_interface):
    """Build the AoiView once (inside use_memo).

    GAUL-2024 admin-code risk: AoiView resolves admin codes against the GAUL-2024
    boundaries on selection / when a model preloaded with admin codes is set. We
    do not preload admin codes here (recipe-load is the Recipe tile's job, Step
    4), so there is no resolve-on-construct that could crash. If/when a recipe
    load path sets admin codes on this model, the resolution must be guarded
    (surface an error, don't crash) — see the deferred load_saved_parameters note.
    """
    view = aoi.AoiView(
        gee=True,
        map_=map_widget,
        model=aoi_date_model,
        gee_interface=gee_interface,
        # Dual-target app (voila sandbox + app-launcher container): SHAPE
        # reads server-local files, which in the container are NOT the user's
        # SEPAL workspace (pysepal skill, AOI Method Restrictions) — excluded
        # as the lowest common denominator. DRAW/ADMIN/ASSET cover the cases.
        methods=["ADMIN1", "ADMIN2", "DRAW", "ASSET"],
    )
    view.flat = True
    return view


def _build_date_pickers(aoi_date_model):
    """Build + bind the two date pickers once (inside use_memo).

    Bindings are attached exactly once here so entered values survive re-renders
    (same rationale as aux_tile.py / overview_tile.py).
    """
    start_picker = sw.DatePicker(label=cm.aoi_tile.start_date_label)
    start_picker.class_ = "pa-1 ma-1"
    end_picker = sw.DatePicker(label=cm.aoi_tile.end_date_label)
    end_picker.class_ = "pa-1 ma-1"

    # Bind picker.v_model <-> model traits (traitlets linking).
    aoi_date_model.bind(start_picker, "start_date").bind(end_picker, "end_date")

    return start_picker, end_picker
