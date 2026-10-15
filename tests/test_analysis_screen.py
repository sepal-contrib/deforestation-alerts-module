"""The analysis screen (spec 2026-10-08 §1-§2): Analysis is not a workflow
step but a screen of its own, a sibling of MapApp. Questionnaire's *Start
analysis* opens it, and so does every ``current_page_view = "analysis_tile"``
writer (Integrate's finish, the Overview's *Review alerts*, a set's *Go*).

MapApp stays mounted while the screen shows: CSS keyed on the screen's
``--on`` class hides its main map and right panel, and its rail (the rail
dialogs, the theme toggle, the language selector) stays usable beside the
screen (user, 2026-10-09). The shell's bridges are compiled from
gui/solara_app.py (it initialises Earth Engine at import), and the screen is
the real ``build_screen`` around stand-in analysis widgets with real
``PaneLayers`` on a real ``AlertViews``.
"""

import ast
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import ipyvuetify as v
import reacton
import solara
from ipywidgets import Widget
from pysepal.i18n import current_locale, set_locale
from traitlets import Bool, HasTraits

from gui.i18n import t
from gui.store.navigation import (
    PAGE_VIEW_TO_SCREEN,
    PAGE_VIEW_TO_WORKFLOW,
    apply_navigation,
    dismiss_dialog,
    page_view_for_rail_step,
    resolve_navigation,
)
from gui.store.state_manager import AppState
from gui.store.workflow_steps import RAIL_ITEMS, STEPS, step_index
from gui.tile.analysis.analysis_tile import SCREEN_CSS, build_screen
from gui.tile.analysis.pane_layers import pane_labels
from gui.widget.set_selector import set_text
from tests._pane_helpers import make_panes

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ helpers
def _shell(*names):
    """The named functions of gui/solara_app.py, compiled against the names they read."""
    from pysepal.solara.components.legend import LegendComponent

    tree = ast.parse((ROOT / "gui" / "solara_app.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names), names
    ns = {"solara": solara, "STEPS": STEPS, "t": t, "current_locale": current_locale,
          "resolve_navigation": resolve_navigation, "apply_navigation": apply_navigation,
          "LegendComponent": LegendComponent}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "gui/solara_app.py", "exec"), ns)
    return ns


def _widgets(root):
    """Every widget under ``root``: its children and its slots' children
    (``solara.Tooltip`` puts its activator in a slot)."""
    out, stack = [], [root]
    while stack:
        w = stack.pop()
        out.append(w)
        kids = list(getattr(w, "children", None) or [])
        for slot in getattr(w, "v_slots", None) or []:
            c = slot.get("children")
            kids += c if isinstance(c, list) else [c]
        stack += [k for k in kids if isinstance(k, Widget)]
    return out


def _by_class(root, name):
    return [w for w in _widgets(root) if name in (getattr(w, "class_", None) or "").split()]


def _screen(state, theme_state=None):
    """``build_screen`` around stand-in analysis widgets (real views)."""
    panes = make_panes()
    widgets = SimpleNamespace(
        panes_widget=panes.widget,
        panes=panes,
        head_before=v.Html(tag="div", class_="analysis-mapbox"),
        head_after=v.Html(tag="div", class_="analysis-mapbox"),
        timeline=v.Html(tag="div", class_="analysis-timeline"),
    )
    controls = v.Html(tag="div", class_="defo-test-controls")
    return build_screen(widgets, controls, state, theme_state)


def _bridges(state, layout, nav=None):
    """The shell's three navigation leaves, rendered together as ``_AppShell`` does.

    ``nav`` stands in for MapApp's step model (``current_step``: the open rail
    dialog's step id, or ``None``)."""
    ns = _shell("_NavBridge", "_ScreenBridge", "_PanelBridge")
    wrappers = {s.key: SimpleNamespace(style_="") for s in STEPS}
    nav = nav if nav is not None else SimpleNamespace(current_step=None)

    @solara.component
    def Shell():
        ns["_NavBridge"](app_state=state, nav_model=nav, mapapp_ref={})
        ns["_ScreenBridge"](app_state=state, layout=layout)
        ns["_PanelBridge"](app_state=state, panel_wrappers=wrappers, mapapp_ref={}, workflow_config={})

    _, rc = reacton.render(Shell(), handle_error=False)
    return rc


def _back(layout):
    (back,) = _by_class(layout.screen, "defo-back-to-workflow")
    return back


def _title(layout):
    (title,) = _by_class(layout.screen, "defo-analysis-title")
    return title.children[0]


def _on(layout):
    return "defo-analysis-screen--on" in layout.screen.class_.split()


def _chevron_right(root):
    return any(isinstance(w, v.Icon) and w.children == ["mdi-chevron-right"] for w in _widgets(root))


class _Theme(HasTraits):
    """pysepal's ThemeState, as far as the screen reads it."""

    dark = Bool(False)


# --------------------------------------------------------------- navigation
def test_the_stepper_ends_at_evaluators():
    keys = [s.key for s in STEPS]
    assert keys[-2:] == ["questionnaire", "evaluators"] and "analysis" not in keys and len(STEPS) == 8
    assert "analysis_tile" not in PAGE_VIEW_TO_WORKFLOW
    assert not hasattr(STEPS[0], "full_stage")


def test_analysis_tile_resolves_to_the_screen_and_keeps_the_step():
    assert PAGE_VIEW_TO_SCREEN == {"analysis_tile": "analysis"}
    assert resolve_navigation("analysis_tile") == ("screen", "analysis")
    state = AppState()
    state.active_step.set(step_index("questionnaire"))
    nav = SimpleNamespace(current_step=3)  # the Overview dialog, where *Review alerts* is
    apply_navigation(None, nav, state, resolve_navigation("analysis_tile"))
    assert state.screen.value == "analysis"
    assert state.active_step.value == step_index("questionnaire")
    assert nav.current_step is None, "the Overview dialog stays open over the screen"


def test_a_workflow_target_shows_the_workflow_screen():
    state = AppState()
    state.screen.set("analysis")
    apply_navigation(None, SimpleNamespace(current_step=None), state, resolve_navigation("integrate_tile"))
    assert state.screen.value == "workflow"
    assert state.active_step.value == step_index("integrate")


def test_closing_or_opening_a_project_returns_to_the_workflow_screen():
    from tests.test_pipeline_header import _project

    state = AppState()
    for install in (lambda: state.load_project_state(_project(n_alerts=3)),
                    lambda: state.new_project_state(_project()),
                    state.close_project_state):
        state.screen.set("analysis")
        install()
        assert state.screen.value == "workflow"


def test_a_repeated_review_after_back_to_workflow_opens_analysis_again():
    """Review Focus 4: *← Workflow* puts the ``current_page_view`` mirror back
    on the step, so a second *Review alerts* (the same "analysis_tile") is a
    change again and reaches ``_NavBridge``."""
    state = AppState()
    layout = _screen(state)
    rc = _bridges(state, layout)
    try:
        state.active_step.set(step_index("integrate"))
        assert state.current_page_view.value == "integrate_tile"
        for _ in range(2):
            state.current_page_view.set("analysis_tile")  # the Overview's *Review alerts*
            assert state.screen.value == "analysis" and _on(layout)
            assert layout.screen.style_ == ""
            assert state.active_step.value == step_index("integrate"), "entering Analysis moved the workflow"
            _back(layout).fire_event("click", None)
            assert state.screen.value == "workflow" and not _on(layout)
            assert layout.screen.style_ == "display: none;"
            assert state.current_page_view.value == "integrate_tile"
            assert state.active_step.value == step_index("integrate")
    finally:
        rc.close()


def test_a_language_switch_on_the_analysis_screen_stays_there():
    """Review Focus 4: a language switch re-runs ``_PanelBridge`` (the panel
    title) and ``_ScreenBridge``. The first must not write the step's view
    into ``current_page_view`` (``_NavBridge`` would send the reviewer back),
    the second relabels the screen and the views."""
    state = AppState()
    layout = _screen(state)
    set_locale("en")
    rc = _bridges(state, layout)
    panes = layout.widgets.panes
    try:
        state.active_step.set(step_index("questionnaire"))
        state.current_page_view.set("analysis_tile")
        assert state.screen.value == "analysis" and _on(layout)
        english = dict(panes.widget.labels)
        assert english == pane_labels()
        set_locale("fr")
        assert state.screen.value == "analysis", "a language switch sent the reviewer back"
        assert state.current_page_view.value == "analysis_tile"
        assert _on(layout) and layout.screen.style_ == ""
        assert panes.widget.labels == pane_labels() != english
        assert _back(layout).children[-1] == "Flux de travail"
        assert _title(layout).startswith("Analyse")
    finally:
        set_locale("en")
        rc.close()


def test_a_language_resolved_while_the_screen_is_hidden_relabels_it():
    """The browser's language arrives after the shell's first render (pysepal
    resolves it when the selector first mounts), while the workflow screen is
    showing. The hidden analysis screen must take it then, not on some later
    switch."""
    state = AppState()
    layout = _screen(state)
    set_locale("en")
    rc = _bridges(state, layout)
    try:
        assert state.screen.value == "workflow" and not _on(layout)
        set_locale("fr")
        assert _back(layout).children[-1] == "Flux de travail"
        assert _title(layout).startswith("Analyse")
    finally:
        set_locale("en")
        rc.close()


def test_a_rail_dialog_opened_on_the_analysis_screen_keeps_the_screen():
    """R30: MapApp's rail stays usable on the screen. A rail click sets
    MapApp's step and the shell's rail mirror writes the item's page view;
    the dialog's close button clears the step. Neither may leave the screen.
    The Overview's *Review alerts*, run from its dialog over the screen,
    closes the dialog and then writes "analysis_tile": the reviewer stays."""
    state = AppState()
    layout = _screen(state)
    nav = SimpleNamespace(current_step=None)
    rc = _bridges(state, layout, nav)
    questionnaire = step_index("questionnaire")
    try:
        state.active_step.set(questionnaire)
        state.current_page_view.set("analysis_tile")
        assert state.screen.value == "analysis" and _on(layout)
        dialogs = [r for r in RAIL_ITEMS if r.display == "dialog"]
        assert [r.step_id for r in dialogs] == [1, 2, 3]
        for item in dialogs:
            nav.current_step = item.step_id  # MapApp opens the dialog
            state.current_page_view.set(page_view_for_rail_step(item.step_id))  # the rail mirror
            assert state.screen.value == "analysis", f"opening {item.key} left the screen"
            assert _on(layout) and layout.screen.style_ == ""
            assert nav.current_step == item.step_id, f"{item.key}'s dialog was closed"
            assert state.active_step.value == questionnaire
            dismiss_dialog(None, nav)  # the dialog's own close button
            assert nav.current_step is None
            assert state.screen.value == "analysis" and _on(layout), f"closing {item.key} left the screen"
        # The Overview's *Review alerts* (OverviewTile._review_alerts) from its
        # dialog over the screen: dismiss, then "analysis_tile".
        nav.current_step = 3
        state.current_page_view.set(page_view_for_rail_step(3))
        dismiss_dialog(None, nav)
        state.current_page_view.set("analysis_tile")
        assert state.screen.value == "analysis" and _on(layout) and layout.screen.style_ == ""
        assert nav.current_step is None, "the Overview dialog stays open over the screen"
        assert state.active_step.value == questionnaire
        # *← Workflow* still works after the rail was used.
        _back(layout).fire_event("click", None)
        assert state.screen.value == "workflow" and not _on(layout)
        assert state.current_page_view.value == STEPS[questionnaire].page_view
    finally:
        rc.close()


# ----------------------------------------------------------- start analysis
def test_the_header_has_no_start_analysis_and_no_next_on_the_last_step():
    """*Start analysis* ends the Questionnaire step now, not the header."""
    from gui.widget.pipeline_header import PipelineHeader
    from tests.test_pipeline_header import _project, _reactive

    last = len(STEPS) - 1
    box, rc = reacton.render(
        PipelineHeader(active_step=last, on_navigate=lambda i: None, project=_reactive(_project(n_alerts=3))),
        handle_error=False,
    )
    try:
        assert not _by_class(box, "defo-start-analysis")
        (nxt,) = [w for w in _widgets(box) if isinstance(w, v.Btn)
                  and any(isinstance(c, v.Icon) and c.children == ["mdi-chevron-right"] for c in w.children)]
        assert nxt.disabled, "the last step has no Next"
    finally:
        rc.close()


def test_the_analysis_rail_item_opens_the_screen():
    """The rail's Analysis entry: its page view is the screen's."""
    from gui.store.navigation import NavAction, resolve_navigation

    (item,) = [r for r in RAIL_ITEMS if r.key == "analysis"]
    assert page_view_for_rail_step(item.step_id) == "analysis_tile"
    assert resolve_navigation("analysis_tile") == NavAction("screen", "analysis")


# ------------------------------------------------------------------- legend
def test_the_legend_is_hidden_on_the_analysis_screen_only():
    """Review Focus 5: the floating legend (z-index 150) would float over the
    analysis screen (4), so it hides there, and it comes back with the
    workflow whatever step the workflow is on."""
    from tests.test_legend_mount import _compiled, _state_with_two_legends

    ns = _compiled("legend_props", "MapLegend")
    state, layers = _state_with_two_legends()
    box, rc = reacton.render(ns["MapLegend"](app_state=state, layers=layers), handle_error=False)

    def legend():
        return next(w for w in _widgets(box) if "legend_data" in w.trait_names())

    try:
        assert legend().visible is True
        state.screen.set("analysis")
        assert legend().visible is False
        state.active_step.set(step_index("integrate"))
        assert legend().visible is False, "a step change on the analysis screen brought it back"
        state.screen.set("workflow")
        assert legend().visible is True
        state.active_step.set(step_index("questionnaire"))
        assert legend().visible is True
    finally:
        rc.close()


# ------------------------------------------------------------------- screen
def _rules(css):
    """``{selector: body}`` of a stylesheet, whitespace collapsed. The rules
    inside an at-rule (``@container``) count as rules of their own: the
    at-rule's prelude is never taken for a selector."""
    def squash(text):
        return " ".join(text.split())

    return {squash(sel): squash(body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)}


def test_only_the_main_map_and_the_right_panel_hide_under_the_screen():
    """MapApp stays mounted (spec §2.3) and its rail stays usable on the screen
    (user, 2026-10-09; R30): the shell's CSS keyed on the screen's ``--on``
    class hides only the main map, the right panel and the panel's "open" tab,
    never the rail, its ``.v-application--wrap`` or the ``.v-application``
    (where menus and dialogs attach). The screen sits right of the rail and
    keeps the full height."""
    from tests.test_app_wiring import _shell_css

    css = _shell_css()
    rules = _rules(css)
    on = "body:has(.defo-analysis-screen--on)"
    hide = f"{on} #map-container, {on} .right-panel, {on} .right-panel-tab"
    assert rules[hide] == "display: none !important;"
    # and one that moves pysepal's log button off the timeline's corner
    pill = f"{on} .pill-wrapper"
    assert [sel for sel in rules if on in sel] == [hide, pill], "two rule sets keyed on the screen"
    assert "display" not in rules[pill], "the log button moves, it is not hidden"
    hidden = [sel for sel, body in rules.items() if "display: none" in body]
    for kept in (".left-drawer", ".v-application--wrap", "sidebar-controls"):
        assert not any(kept in sel for sel in hidden), f"{kept} is hidden: the rail must stay usable"
    assert not any(part.strip().endswith(".v-application") for sel in hidden for part in sel.split(","))
    assert "defo-map-hidden" not in css and ".defo-stage" not in css

    root = _rules(SCREEN_CSS)[".defo-analysis-screen"]
    for decl in ("position: fixed;", "top: 0;", "bottom: 0;", "left: var(--sepal-drawer-width, 60px);",
                 "right: 0;", "z-index: 4;", "transition: left 0.3s ease;"):
        assert decl in root, f"the screen root lacks {decl!r}: it sits right of the rail, full height"
    assert "inset" not in root and "--sepal-bottom-reserved" not in root and "--sepal-right-panel-width" not in root


def test_leaving_the_screen_shows_mapapp_before_hiding_the_views():
    """Review Focus 5: the class goes first, so MapApp's main map and right
    panel are displayed again while the screen still covers the map; then the
    screen hides, and the views' hide nudge (a window resize) finds the main
    map displayed. The frontend applies the two writes in the order they are
    sent."""
    layout = _screen(AppState())
    ops = []
    layout.screen.observe(lambda c: ops.append((c["name"], c["new"])), names=["class_", "style_"])
    layout.show(True)
    assert [name for name, _ in ops] == ["style_", "class_"]
    assert ops[0][1] == "" and "defo-analysis-screen--on" in ops[1][1]
    del ops[:]
    layout.show(False)
    assert [name for name, _ in ops] == ["class_", "style_"]
    assert "defo-analysis-screen--on" not in ops[0][1] and ops[1][1] == "display: none;"


def test_the_screen_holds_the_back_button_the_views_the_strip_and_the_controls():
    layout = _screen(AppState())
    root = layout.screen
    style = root.children[0]
    assert isinstance(style, v.Html) and style.tag == "style" and style.children == [SCREEN_CSS]
    assert len(_by_class(root, "defo-back-to-workflow")) == 1
    (left,) = _by_class(root, "defo-analysis-left")
    (right,) = _by_class(root, "defo-analysis-right")
    assert [c.class_ for c in left.children] == ["analysis-heads", "analysis-panes-slot", "analysis-strip"]
    heads, slot, strip = left.children
    assert heads.children == [layout.widgets.head_before, layout.widgets.head_after]
    assert slot.children == [layout.widgets.panes_widget]
    assert strip.children == [layout.widgets.timeline]
    assert right.children == [layout.controls]
    assert "100vh" not in SCREEN_CSS, "a full-height flex layout: no height computed from the viewport"
    assert root.style_ == "display: none;", "the screen is built hidden"
    assert {"defo-analysis-screen", "analysis-stage"} <= set(root.class_.split())


def test_the_back_button_returns_to_the_workflow():
    state = AppState()
    layout = _screen(state)
    state.active_step.set(step_index("questionnaire"))
    state.screen.set("analysis")
    _back(layout).fire_event("click", None)
    assert state.screen.value == "workflow"
    assert state.active_step.value == step_index("questionnaire"), "back to the step Analysis was entered from"
    assert _back(layout).children[-1] == t("app.panel_workflow_title")


def test_the_screen_follows_the_theme():
    """The rail's theme toggle (pysepal's ThemeState) re-themes the screen
    while it shows (R30): ``build_screen`` observes ``theme_state.dark``, and
    the shell hands MapApp's ``theme_state`` down to it."""
    from gui.tile.analysis.analysis_tile import build_analysis_layout
    from tests.test_app_wiring import _src

    theme = _Theme()
    layout = _screen(AppState(), theme)

    def classes():
        return set(layout.screen.class_.split())

    assert {"analysis-stage", "theme--light"} <= classes() and "analysis-stage--dark" not in classes()
    theme.dark = True
    assert {"analysis-stage--dark", "theme--dark"} <= classes() and "theme--light" not in classes()
    layout.show(True)
    assert {"defo-analysis-screen--on", "theme--dark"} <= classes()
    theme.dark = False
    assert {"defo-analysis-screen--on", "theme--light"} <= classes() and "theme--dark" not in classes()

    shell = _src("gui/solara_app.py")
    memo = shell[shell.index("analysis_layout = solara.use_memo("):]
    memo = memo[:memo.index("dependencies=[]")]
    assert "theme_state=theme_state," in memo, "the shell must hand MapApp's theme state to the screen"
    assert "build_screen(widgets, controls, app_state, theme_state, labels=labels)" in inspect.getsource(
        build_analysis_layout)


def test_the_title_names_the_project_and_the_active_set():
    from tests.test_pipeline_header import _project

    set_locale("en")
    state = AppState()
    layout = _screen(state)
    assert _title(layout) == "Analysis"
    p = _project(n_alerts=3)
    state.load_project_state(p)
    assert _title(layout) == f"Analysis · demo · {set_text(p, p.alert_sets['x'])}"
    state.close_project_state()
    assert _title(layout) == "Analysis"


def test_every_screen_css_rule_is_scoped_to_the_analysis_screen():
    """SCREEN_CSS is injected into the whole page: an unscoped rule (the
    text-field one was) restyles every text field of the app."""
    for selector in _rules(SCREEN_CSS):
        for part in selector.split(","):
            assert part.strip().startswith(".defo-analysis") or part.strip().startswith(
                ".analysis-"
            ), f"unscoped selector {part.strip()!r}"
    assert ".defo-analysis-screen .v-text-field .v-input__control .v-input__slot" in _rules(SCREEN_CSS)
