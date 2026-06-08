"""Alert navigation row (prev / next / go-to + id field) for the analysis tile.

Architecture layer: view. Ports the OLD ``AnalysisTile`` navigation-bar card
(``card01`` + the ``navigate`` handler). The OLD tile mutated
``analyzed_alerts_model.actual_alert_id`` with wrap-around arithmetic; per the
foundation contract, navigation is now driven through ``app_state`` (the source
of truth for the alerts table + current index):

    * prev  -> ``app_state.go_prev()``
    * next  -> ``app_state.go_next()``
    * "Go"  -> ``app_state.go_to(int(field_value))``

``app_state.go_to`` already clamps to ``[0, max_alert_id]`` (and returns early-ish
when no gdf is loaded, since ``max_alert_id`` is -1), so the buttons are safe to
press at smoke time with no alerts loaded.

The id TextField is a *memoized raw* ipyvuetify widget (not a reacton element) so
the parent's ``view_actual_alert`` effect can imperatively set ``.v_model`` to
reflect the current alert id — same imperative-widget rationale as the rest of the
tile. We seed its initial value from ``app_state.actual_alert_id`` and keep it in
sync from the parent effect.
"""

import ipyvuetify as v
import solara
from pysepal import sepalwidgets as sw


@solara.component
def AlertNavigator(app_state, id_field):
    """Prev / next / go-to navigation row.

    Parameters
    ----------
    app_state : AppState
        Source of truth for navigation; ``go_prev`` / ``go_next`` / ``go_to`` move
        the current alert index, and ``actual_alert_id`` reflects it.
    id_field : ipyvuetify.TextField
        The memoized alert-id text field (owned by the parent so its
        ``view_actual_alert`` effect can sync ``.v_model``). Built once in the
        parent and passed in.
    """
    prev_button = solara.use_memo(
        lambda: sw.Btn(
            icon="fa-solid fa-backward-step",
            color="primary",
            outlined=True,
            small=True,
        ),
        dependencies=[],
    )
    next_button = solara.use_memo(
        lambda: sw.Btn(
            icon="fa-solid fa-forward-step",
            color="primary",
            outlined=True,
            small=True,
        ),
        dependencies=[],
    )
    go_button = solara.use_memo(
        lambda: sw.Btn("Go", color="primary", outlined=True, small=True),
        dependencies=[],
    )

    # Wire click handlers once. Navigation goes through app_state (the source of
    # truth); the parent's view_actual_alert effect (keyed on actual_alert_id)
    # then refreshes the maps + cards + id field.
    def _wire():
        def on_prev(*_):
            app_state.go_prev()

        def on_next(*_):
            app_state.go_next()

        def on_go(*_):
            try:
                target = int(id_field.v_model)
            except (TypeError, ValueError):
                return
            app_state.go_to(target)

        prev_button.on_event("click", on_prev)
        next_button.on_event("click", on_next)
        go_button.on_event("click", on_go)

        def _cleanup():
            prev_button.on_event("click", on_prev, remove=True)
            next_button.on_event("click", on_next, remove=True)
            go_button.on_event("click", on_go, remove=True)

        return _cleanup

    solara.use_effect(_wire, dependencies=[])

    nav_card = solara.use_memo(
        lambda: v.Card(
            class_="pa-2 ma-2 d-flex justify-center",
            hover=True,
            children=[prev_button, next_button, id_field, go_button],
        ),
        dependencies=[],
    )
    solara.display(nav_card)
