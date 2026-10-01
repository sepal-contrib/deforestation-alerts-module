"""Pure navigation logic for the Workflow-panel shell.

Tiles keep writing ``app_state.current_page_view`` (the original Voila app's
navigation signal). This module maps that string to either a **workflow**
index (a step in the right panel's Workflow view, driven by
``app_state.active_step``) or a **rail** step id (a MapApp ``steps_data``
entry: a dialog, or the Overview panel view). Side-effect-free (no solara
rendering, no EE, no widgets) so it is unit-testable offline;
``gui.solara_app._NavBridge`` applies the result.

All maps derive from ``gui.store.workflow_steps`` — never hand-edit ids here.
"""

from collections import namedtuple

from gui.store.workflow_steps import RAIL_ITEMS, STEPS, rail_item_for_step

PAGE_VIEW_TO_WORKFLOW = {s.page_view: i for i, s in enumerate(STEPS)}
PAGE_VIEW_TO_RAIL = {r.page_view: r.step_id for r in RAIL_ITEMS}
RAIL_STEP_TO_PAGE_VIEW = {r.step_id: r.page_view for r in RAIL_ITEMS}

#: ``kind`` is ``"workflow"`` (``target`` = index into STEPS) or ``"rail"``
#: (``target`` = MapApp step id of the rail item).
NavAction = namedtuple("NavAction", ["kind", "target"])


def page_view_for_rail_step(step_id):
    """current_page_view name of a rail step id, or ``None``."""
    return RAIL_STEP_TO_PAGE_VIEW.get(step_id)


def resolve_navigation(view):
    """Map a ``current_page_view`` string to a :class:`NavAction` or ``None``."""
    if view in PAGE_VIEW_TO_WORKFLOW:
        return NavAction("workflow", PAGE_VIEW_TO_WORKFLOW[view])
    if view in PAGE_VIEW_TO_RAIL:
        return NavAction("rail", PAGE_VIEW_TO_RAIL[view])
    return None


def dismiss_dialog(mapapp, nav_model):
    """Close the open rail dialog, as its own close button does.

    MapApp.vue's ``closeDialog`` closes the dialog and clears the step;
    from Python, ``current_step = None`` (linked through ``nav_model``)
    clears the step and ``step_open = False`` closes the dialog. Both are
    no-ops when no dialog is open. A panel item (Overview) stays current.
    """
    item = rail_item_for_step(nav_model.current_step)
    if item is not None and item.display == "dialog":
        nav_model.current_step = None
    if mapapp is not None:
        mapapp.step_open = False


def _open_panel(mapapp):
    """Dismiss a lingering dialog and make sure the right panel is open.

    ``step_open=False`` is MapApp.vue's close watcher; the Vue ``current_step``
    watcher does not apply ``right_panel_action`` for programmatic changes, so
    the panel is opened explicitly.
    """
    if mapapp is not None:
        mapapp.step_open = False
        mapapp.vue_handle_right_panel_action("open")


def apply_navigation(mapapp, nav_model, app_state, action):
    """Apply a resolved :class:`NavAction`.

    Workflow target: open the panel, return it to the Workflow view, clear the
    MapApp step (un-highlights a panel rail item such as Overview) and move the
    workflow position. Programmatic navigation is deliberately NOT gated: a
    tile jumps forward only after producing the output that unlocks the
    target, and the header's gates protect user clicks.

    Rail target: a panel item (Overview) opens the panel and switches the view;
    a dialog item only sets the MapApp step and MapApp opens the dialog itself.
    Dialogs never change ``panel_view``.

    ``mapapp`` may be ``None`` (widget not mounted yet) — the state still
    moves; only the panel/dialog handling is skipped.
    """
    if action.kind == "workflow":
        _open_panel(mapapp)
        nav_model.current_step = None
        app_state.panel_view.set("workflow")
        app_state.active_step.set(action.target)
        return
    item = rail_item_for_step(action.target)
    if item is not None and item.display == "panel":
        _open_panel(mapapp)
        app_state.panel_view.set(item.key)
    nav_model.current_step = action.target
