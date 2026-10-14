"""Pure navigation logic for the Workflow-panel shell.

Tiles keep writing ``app_state.current_page_view`` (the original Voila app's
navigation signal). This module maps that string to a **workflow** index (the
right panel's step, driven by ``app_state.active_step``), a **rail** step id
(a MapApp ``steps_data`` entry: a dialog -- About, Project and the Overview
dashboard) or a **screen** (``app_state.screen``: "analysis_tile" opens the
analysis screen, MapApp's sibling). Side-effect-free (no solara rendering, no
EE, no widgets) so it is unit-testable offline; ``gui.solara_app._NavBridge``
applies the result.

All maps derive from ``gui.store.workflow_steps`` — never hand-edit ids here.
"""

from collections import namedtuple

from gui.store.workflow_steps import RAIL_ITEMS, STEPS, rail_item_for_step

PAGE_VIEW_TO_WORKFLOW = {s.page_view: i for i, s in enumerate(STEPS)}
#: The rail's dialogs. Its "screen" items (Analysis) navigate by their page
#: view through ``PAGE_VIEW_TO_SCREEN`` instead.
PAGE_VIEW_TO_RAIL = {r.page_view: r.step_id for r in RAIL_ITEMS if r.display == "dialog"}
RAIL_STEP_TO_PAGE_VIEW = {r.step_id: r.page_view for r in RAIL_ITEMS}
#: Page views that open a screen of their own instead of a step or a dialog.
#: Integrate's finish, the Overview's *Review alerts* / *Go* and
#: Questionnaire's *Start analysis* all write "analysis_tile".
PAGE_VIEW_TO_SCREEN = {"analysis_tile": "analysis"}

#: ``kind`` is ``"workflow"`` (``target`` = index into STEPS), ``"rail"``
#: (``target`` = MapApp step id of the rail item) or ``"screen"``
#: (``target`` = an ``app_state.screen`` value).
NavAction = namedtuple("NavAction", ["kind", "target"])


def page_view_for_rail_step(step_id):
    """current_page_view name of a rail step id, or ``None``."""
    return RAIL_STEP_TO_PAGE_VIEW.get(step_id)


def resolve_navigation(view):
    """Map a ``current_page_view`` string to a :class:`NavAction` or ``None``."""
    if view in PAGE_VIEW_TO_SCREEN:
        return NavAction("screen", PAGE_VIEW_TO_SCREEN[view])
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
    no-ops when no dialog is open.
    """
    if rail_item_for_step(nav_model.current_step) is not None:
        nav_model.current_step = None
    if mapapp is not None:
        mapapp.step_open = False


def _open_panel(mapapp):
    """Dismiss a lingering dialog and make sure the right panel is open.

    ``step_open=False`` is MapApp.vue's close watcher; the Vue ``current_step``
    watcher does not apply a panel action for programmatic changes, so the
    panel is opened explicitly.
    """
    if mapapp is not None:
        mapapp.step_open = False
        mapapp.vue_handle_right_panel_action("open")


def apply_navigation(mapapp, nav_model, app_state, action):
    """Apply a resolved :class:`NavAction`.

    Screen target (Analysis): close an open rail dialog (the Overview's
    *Review alerts* runs from one) and switch the screen. ``active_step`` is
    left alone, so *← Workflow* returns to the step Analysis was entered from.

    Workflow target: show the workflow screen, open the panel, dismiss an open
    dialog, clear the MapApp step (which un-highlights its rail item) and move
    the workflow position. Programmatic navigation is deliberately NOT gated:
    a tile jumps forward only after producing the output that unlocks the
    target, and the header's gates protect user clicks.

    Rail target: only the MapApp step is set; MapApp opens the dialog itself,
    and the right panel is left as the user left it behind the dialog.

    ``mapapp`` may be ``None`` (widget not mounted yet) — the state still
    moves; only the panel/dialog handling is skipped.
    """
    if action.kind == "screen":
        dismiss_dialog(mapapp, nav_model)
        app_state.screen.set(action.target)
        return
    if action.kind == "workflow":
        _open_panel(mapapp)
        nav_model.current_step = None
        app_state.screen.set("workflow")
        app_state.active_step.set(action.target)
        return
    nav_model.current_step = action.target
