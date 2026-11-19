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
#: Evaluation's *Start analysis* all write "analysis_tile".
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


def dismiss_dialog(rail):
    """Close the open rail dialog, as its own close button does.

    ``rail`` is MapApp's ``current_step`` prop: MapApp closes the dialog when
    it changes to ``None``. A no-op when no dialog is open.
    """
    if rail_item_for_step(rail.value) is not None:
        rail.set(None)


def apply_navigation(rail, panel_open, app_state, action):
    """Apply a resolved :class:`NavAction`.

    ``rail`` and ``panel_open`` are the shell's reactives behind MapApp's
    ``current_step`` and ``right_panel_open`` props.

    Screen target (Analysis): close an open rail dialog (the Overview's
    *Review alerts* runs from one) and switch the screen. ``active_step`` is
    left alone, so *← Workflow* returns to the step Analysis was entered from.

    Workflow target: show the workflow screen, open the panel, clear the
    MapApp step (which closes an open dialog and un-highlights its rail item)
    and move the workflow position. Programmatic navigation is deliberately
    NOT gated: a tile jumps forward only after producing the output that
    unlocks the target, and the header's gates protect user clicks.

    Rail target: only ``rail`` (MapApp's step) is set; MapApp opens the dialog
    itself, and the right panel is left as the user left it behind the dialog.
    """
    if action.kind == "screen":
        dismiss_dialog(rail)
        app_state.screen.set(action.target)
        return
    if action.kind == "workflow":
        panel_open.set(True)
        rail.set(None)
        app_state.screen.set("workflow")
        app_state.active_step.set(action.target)
        return
    rail.set(action.target)


def on_rail_step(rail, app_state, sid):
    """MapApp activated rail step ``sid`` (a click, or ``None`` on close).

    The item's page view is mirrored into ``current_page_view``, so a
    repeated trigger reaches ``_NavBridge`` again; the panel is left as the
    user left it behind the dialog, and ``None`` changes nothing (spec D13).
    """
    # First, always: rail is MapApp's current_step prop, which reacton pushes
    # only on a change, so clearing it below must start from this step.
    rail.set(sid)
    view = page_view_for_rail_step(sid)
    if view is not None and app_state.current_page_view.value != view:
        app_state.current_page_view.value = view
    if view in PAGE_VIEW_TO_SCREEN:
        # A screen is not a dialog left open: clear the step (and its rail
        # highlight) so the next click is a change again, even one made
        # while the screen already shows.
        rail.set(None)
