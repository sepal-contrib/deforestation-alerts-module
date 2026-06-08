"""AOI/Date tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara + ipyvuetify/pysepal for UI only. The
GEE-heavy search runs in ``solara.lab.use_task(prefer_threaded=True)``; the model
mutations and the ccdc dictionary observer stay on the (service) helper functions.

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
  inputs); the click handler calls ``search_task()`` and the button shows
  ``search_task.pending`` as its loader.
* **Map + AoiView + DatePickers are memoized once** (``dependencies=[]``) so the
  widget identities and the ``.bind()`` chain are stable across re-renders.
"""

import logging

import ipyvuetify as v
import solara
from pysepal import aoi
from pysepal import sepalwidgets as sw
from pysepal.mapping import SepalMap

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.aoi_helper import (
    add_ccdc_alerts_dictionary,
    check_aoi_inputs,
    create_available_alert_dictionary,
    create_basic_alerts_dictionary,
    create_filtered_alert_raster_dictionary,
    remove_ccdc_alerts_dictionary,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.aoi")


class _InputsShim:
    """Adapter exposing the attribute shape ``check_aoi_inputs`` expects.

    The service helper ``check_aoi_inputs(self)`` reads
    ``self.aoi_view.model.feature_collection`` and ``self.start_date.v_model`` /
    ``self.end_date.v_model`` (it was written against the OLD widget-class
    ``self``). Rather than fork the still-shared helper, we hand it this thin
    shim around the memoized widgets so the validation logic stays in one place.
    """

    def __init__(self, aoi_view, start_picker, end_picker):
        self.aoi_view = aoi_view
        self.start_date = start_picker
        self.end_date = end_picker


def _update_dictionary_ccdc(aux_model, alert_filter_model):
    """Add/remove the CCDC entry in ``alert_filter_model.alerts_dictionary``.

    Same logic as the OLD tile's ``update_dictionary_ccdc`` method, lifted to a
    module function so the traitlets observer handler can call it. Guarded so a
    missing ``CCDC`` key (double-clear) does not raise.
    """
    dictionary = alert_filter_model.alerts_dictionary
    if not isinstance(dictionary, dict):
        # alerts_dictionary not initialized yet (e.g. EE init failed) — nothing
        # to mutate; surface in logs rather than crash the observer thread.
        logger.warning("ccdc observer fired before alerts_dictionary was a dict")
        return
    try:
        if aux_model.ccdc_layer:
            add_ccdc_alerts_dictionary(dictionary, aux_model.ccdc_layer)
        elif aux_model.ccdc_layer is None and "CCDC" in dictionary:
            remove_ccdc_alerts_dictionary(dictionary)
    except Exception:
        logger.exception("Failed to update CCDC entry in alerts_dictionary")


@solara.component
def AoiTile(aoi_date_model, alert_filter_model, aux_model, app_state, gee_interface):
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
    """
    # ------------------------------------------------------------------
    # Initialize the alerts dictionary + available lists exactly once.
    # OLD __init__ did this eagerly; create_basic_alerts_dictionary hits EE, so
    # we run it in a memo (dependencies=[]) rather than on every render. If EE is
    # unavailable we fall back to {} and log — the tile still renders.
    # ------------------------------------------------------------------
    def _init_models():
        try:
            alert_filter_model.alerts_dictionary = create_basic_alerts_dictionary()
        except Exception:
            logger.exception("Failed to build basic alerts dictionary (EE down?)")
            alert_filter_model.alerts_dictionary = {}
        alert_filter_model.available_alerts_list = []
        alert_filter_model.available_alerts_raster_list = []
        return True

    solara.use_memo(_init_models, dependencies=[])

    # ------------------------------------------------------------------
    # Memoized map + AoiView. The map fills the panel (like Overview) so the
    # fullscreen main map does not bleed through. AoiView's model IS the recipe
    # model, so selection writes straight into aoi_date_model.
    # ------------------------------------------------------------------
    map_widget = solara.use_memo(
        lambda: _build_map(gee_interface), dependencies=[]
    )

    aoi_view = solara.use_memo(
        lambda: _build_aoi_view(aoi_date_model, map_widget, gee_interface),
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
    # ------------------------------------------------------------------
    def _register_ccdc_observer():
        def handler(change):
            _update_dictionary_ccdc(aux_model, alert_filter_model)

        aux_model.observe(handler, "ccdc_layer")
        return lambda: aux_model.unobserve(handler, "ccdc_layer")

    solara.use_effect(_register_ccdc_observer, dependencies=[])

    # ------------------------------------------------------------------
    # Search task. dependencies=None => never auto-runs; only fires when the
    # click handler calls search_task(). Does the EE-ish work on a worker thread
    # and writes the result lists onto alert_filter_model.
    # ------------------------------------------------------------------
    def _run_search():
        # AOI fields are already on aoi_date_model (AoiView wrote them); dates are
        # already bound from the pickers. Validate via the shared helper through
        # the shim, then run the two create_* service functions.
        shim = _InputsShim(aoi_view, start_picker, end_picker)
        feature_collection, date1, date2 = check_aoi_inputs(shim)

        dictionary = alert_filter_model.alerts_dictionary
        if not isinstance(dictionary, dict):
            # Defensive: should have been set by _init_models.
            dictionary = create_basic_alerts_dictionary()
            alert_filter_model.alerts_dictionary = dictionary

        available = create_available_alert_dictionary(
            dictionary, feature_collection, date1, date2
        )
        alert_filter_model.available_alerts_list = available

        alert_filter_model.available_alerts_raster_list = (
            create_filtered_alert_raster_dictionary(
                available,
                feature_collection,
                date1,
                date2,
                aux_model.ccdc_layer,
            )
        )

        # Preserve the OLD tile's intent to jump to the Filter tile.
        # TODO(navigation foundation): wiring app_state.current_page_view ->
        # MapApp.current_step (so the step panel actually switches when this is
        # set) is a separate cross-tile navigation concern not yet wired in this
        # app. Driving MapApp.current_step from a tile risks a controlled/
        # uncontrolled sync loop, so it is deferred to a dedicated follow-up.
        app_state.current_page_view.value = "filter_alerts"
        return True

    search_task = solara.lab.use_task(
        _run_search, dependencies=None, prefer_threaded=True, raise_error=False
    )

    def _on_search_click(*_):
        # Thin callback: just trigger the task; the task does the work + writes.
        search_task()

    # ------------------------------------------------------------------
    # Render layout: controls on the right, AOI map filling the left.
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

    with solara.Row(style="height: 100%; gap: 0;"):
        # Left: AOI map (fills remaining space)
        with solara.Column(style="flex: 1 1 auto; min-width: 0; overflow: hidden;"):
            solara.display(map_widget)

        # Right: AOI + date selection + search
        with solara.Column(style="flex: 0 0 22rem; overflow: auto; padding: 0;"):
            solara.display(aoi_card)
            solara.display(date_card)
            solara.Button(
                label=cm.aoi_tile.search_button,
                on_click=_on_search_click,
                loading=search_task.pending,
                disabled=search_task.pending,
                color="primary",
                class_="ma-3",
            )
            if search_task.error:
                solara.Error(
                    f"Search failed: {search_task.exception}", class_="ma-3"
                )


def _build_map(gee_interface):
    """Build the AOI map once (inside use_memo), with the SATELLITE basemap.

    The OLD tile called ``map_1.add_basemap("SATELLITE")``; we keep that for
    visual parity. Guarded so a basemap-load failure does not crash the memo.
    """
    map_widget = SepalMap(gee_interface=gee_interface)
    try:
        map_widget.add_basemap("SATELLITE")
    except Exception:
        logger.exception("Failed to add SATELLITE basemap to AOI map")
    return map_widget


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
        methods=["ADMIN1", "ADMIN2", "SHAPE", "DRAW", "ASSET"],
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
