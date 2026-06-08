"""Pure navigation logic for the single-map Solara shell.

Translates the ``app_state.current_page_view`` name (the original Voila app's
navigation signal) into the ``MapApp`` step id + the per-transition right-panel /
dialog actions. Kept side-effect-free (no solara, no EE, no widgets) so it is
unit-testable offline; ``gui.solara_app._NavBridge`` applies the result to the
rendered ``MapApp`` widget.

Layout note: the seven drawer tiles map 1:1 to step ids in drawer order. Only
1/3/5/7 are exercised by the current navigation triggers; the rest are mapped for
completeness and to back the step -> view reverse sync (so a manual drawer click
keeps ``current_page_view`` faithful, which in turn lets a *repeated* trigger
re-fire). See docs/superpowers/specs/2026-06-10-programmatic-tile-navigation-design.md.
"""

from collections import namedtuple

# current_page_view name -> drawer step id (drawer order). "filter_alerts" (not
# "filter_tile") is intentional: it is the exact string the AOI tile sets.
PAGE_VIEW_TO_STEP = {
    "about_tile": 1,
    "recipe_tile": 2,
    "aux_tile": 3,
    "aoi_tile": 4,
    "filter_alerts": 5,
    "overview_tile": 6,
    "analysis_tile": 7,
}

STEP_TO_PAGE_VIEW = {step: view for view, step in PAGE_VIEW_TO_STEP.items()}

#: Result of :func:`resolve_navigation`.
#:
#: * ``step_id``      -- the ``MapApp.current_step`` to activate.
#: * ``panel_action`` -- ``"open"`` for right-panel tiles, ``"close"`` otherwise;
#:   fed to ``MapApp.vue_handle_right_panel_action``.
#: * ``close_dialog`` -- ``True`` when the target is not a dialog, so the bridge
#:   sets ``MapApp.step_open = False`` to dismiss any lingering dialog (a no-op
#:   when none is open).
NavAction = namedtuple("NavAction", ["step_id", "panel_action", "close_dialog"])


def page_view_for_step(step_id):
    """Return the current_page_view name for a step id, or ``None`` if unknown."""
    return STEP_TO_PAGE_VIEW.get(step_id)


def resolve_navigation(view, step_display):
    """Decide the navigation actions for a target ``current_page_view``.

    Args:
        view: the ``current_page_view`` string (e.g. ``"filter_alerts"``).
        step_display: mapping of ``step_id -> display`` (``"panel"`` / ``"dialog"``
            / ``"step"``), built by the shell from its ``steps_data``.

    Returns:
        A :class:`NavAction`, or ``None`` when ``view`` is not a known tile (the
        caller should no-op).
    """
    step_id = PAGE_VIEW_TO_STEP.get(view)
    if step_id is None:
        return None
    display = step_display.get(step_id)
    panel_action = "open" if display == "panel" else "close"
    close_dialog = display != "dialog"
    return NavAction(step_id=step_id, panel_action=panel_action, close_dialog=close_dialog)


def apply_navigation(mapapp, nav_model, action):
    """Apply a resolved :class:`NavAction` to the rendered MapApp + nav model.

    The Vue ``current_step`` watcher only swaps content + opens dialog targets, so
    we drive the right panel / lingering dialog explicitly (the part a drawer
    click does but the watcher does not):

    1. ``mapapp.vue_handle_right_panel_action(panel_action)`` -- open the right
       panel for panel tiles, close it otherwise.
    2. when ``close_dialog`` -- set ``mapapp.step_open = False`` to dismiss any
       open dialog (the Vue ``step_open`` watcher; a no-op when none is open).
    3. set ``nav_model.current_step`` (linked to ``MapApp.current_step``) to switch
       the active step.

    ``mapapp`` may be ``None`` (widget not mounted yet) -- the step is still set so
    content switches; the panel/dialog handling is simply skipped.
    """
    if mapapp is not None:
        mapapp.vue_handle_right_panel_action(action.panel_action)
        if action.close_dialog:
            mapapp.step_open = False
    nav_model.current_step = action.step_id
