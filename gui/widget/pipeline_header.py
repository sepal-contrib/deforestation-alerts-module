"""Compact pipeline header for the workflow panel.

Two levels (spec: workflow two levels §1) in one strip: per level (Prepare
alerts | Set up review) a clickable label over its segments (one cell per
step) mapping the pipeline (has-outputs / empty / locked, with a ring on the current step), a
title row with the active step's name, output count and position, a unified
clickable title (badge + name + count + caret) that opens the jump menu,
Back / Next buttons, and the step's instruction line with its info button.
``STEPS`` stays one flat list: Back / Next cross the level boundary.

Semantics are outputs, not completion — steps are revisitable workspaces,
never "done". Segment/badge data all derives from the STEPS registry.
"""

import reacton.ipyvuetify as rv
import solara
import solara.lab

from gui.i18n import t
from gui.store.workflow_steps import LEVELS, STEPS, StepStatus, nav_targets, plan_once, step_states, steps_of
from gui.widget.help import InfoButton
from gui.widget.text_style import MUTED

# Empty / locked tones stay theme-neutral grey; the HAS_OUTPUTS fill and the
# current-step ring derive from the live Vuetify "primary" colour at render
# time (see PipelineHeader) so the strip matches the app accent in both modes.
_SEG_EMPTY = "rgba(128, 128, 128, 0.28)"
_SEG_LOCKED = (
    "repeating-linear-gradient(90deg, rgba(128,128,128,0.30) 0 3px,"
    " rgba(128,128,128,0.10) 3px 6px)"
)


_LEVEL_LABELS = {"prepare": "workflow.level_prepare", "review": "workflow.level_review"}


def _rgba(hex_color: str, alpha: float) -> str:
    """`#rrggbb` -> `rgba(r, g, b, alpha)` (for the translucent current-step ring)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def count_text(spec, p) -> str:
    """Human badge for a step: outputs count, AOI name, 'none' for an
    optional step left empty and passed, or 'nothing yet'."""
    c = spec.count(p)
    if isinstance(c, str) and c:
        return c
    n = int(c or 0)
    if n > 0 and spec.count_key:
        return t(spec.count_key, count=n)
    if spec.passed_key and spec.passed(p):
        return t(spec.passed_key)
    return t("workflow.count_empty")


# A native <button> reset to bare text: Enter / Space and focus come for free.
_BUTTON_RESET = "background: none; border: 0; margin: 0; font: inherit; color: inherit;"


@solara.component
def _SegmentCell(key: str, label: str, tip: str, seg_style: str, locked: bool, current: bool, on_activate):
    """One segment of the pipeline strip.

    Its own component so the ``rv.use_event`` click hook is called exactly once
    at this component's top level — never inside the caller's loop over STEPS
    (rules of hooks). Locked cells keep the handler attached and no-op inside
    it, so lock/unlock never changes the hook count. ``data-step-key`` lets the
    headless driver click a step by key.
    """
    cursor = "default" if locked else "pointer"
    with rv.Html(
        tag="button",
        style_=f"{_BUTTON_RESET} display: block; flex: 1; min-width: 0; padding: 7px 0; cursor: {cursor};",
        attributes={
            "title": tip, "data-step-key": key, "aria-label": label,
            "aria-current": "step" if current else "false", "aria-disabled": "true" if locked else "false",
        },
    ) as cell:
        rv.Html(tag="div", style_=seg_style)
    rv.use_event(cell, "click", lambda *_: not locked and on_activate())


@solara.component
def _LevelLabel(level: str, label: str, tip: str, done: bool, active: bool, locked: bool, primary: str, on_activate):
    """One level's label, above its bars. Same per-cell hook rule as
    ``_SegmentCell``; a locked level keeps its handler and the caller's
    handler only shows the reason. ``data-level-key`` lets the headless
    driver click a level by key. A long label is cut, its tooltip whole."""
    tone = primary if active else "rgba(128, 128, 128, 0.9)"
    with rv.Html(
        tag="button",
        class_="defo-level-label defo-level-label--on" if active else "defo-level-label",
        style_=f"{_BUTTON_RESET} display: block; width: 100%; padding: 0 1px; text-align: left;"
        f" font-size: 11px; font-weight: 700; line-height: 16px; color: {tone};"
        " white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
        f" cursor: {'not-allowed' if locked else 'pointer'};",
        attributes={
            "title": tip, "data-level-key": level, "aria-label": label,
            "aria-disabled": "true" if locked else "false",
        },
    ) as button:
        if locked:
            rv.Icon(children=["mdi-lock-outline"], x_small=True, style_="color: inherit; margin: -2px 3px 0 0;")
        rv.Html(tag="span", children=[f"✓ {label}" if done else label])
    rv.use_event(button, "click", lambda *_: on_activate())


@solara.component
def _JumpMenuRow(
    index: int, number: int, label: str, sub_text: str, locked: bool, active: bool, on_jump
):
    """One row of the step-jump dropdown.

    Same per-row-component rule as ``_SegmentCell``. ``on_jump`` re-checks
    locked itself — Vuetify's ``pointer-events: none`` on a disabled item is
    only the first guard.
    """
    with rv.ListItem(
        disabled=locked,
        link=not locked,
        color="primary",
        input_value=active,
    ) as row:
        # Number for reachable steps, lock glyph otherwise (mockup: no
        # status icons).
        if locked:
            rv.Icon(
                children=["mdi-lock-outline"],
                small=True,
                style_="margin-right: 10px;",
            )
        else:
            solara.Text(
                str(number),
                style="width: 18px; text-align: center;"
                " font-weight: 600; font-size: 12px;"
                " margin-right: 10px;",
            )
        with rv.ListItemContent():
            rv.ListItemTitle(children=[label])
        solara.Text(
            sub_text,
            style=MUTED + "font-size: 11px; margin-left: 12px; text-align: right;",
        )
    rv.use_event(row, "click", lambda *_: on_jump(index))


@solara.component
def PipelineHeader(active_step: int, on_navigate, project, dark=None):
    """Pipeline map + step navigation.

    Takes the ``project`` reactive (not its value — reacton's prop-equality
    bailout would eat project-only changes) plus the active index and a
    navigate callback.

    ``dark`` is an optional reactive mirroring pysepal's ``ThemeState.dark``.
    This app's theme is owned by ``theme_state``/``MapApp``, NOT by Solara's own
    dark mode, so ``solara.lab.use_dark_effective()`` reports the wrong value
    here (it is always True, which painted the strip and badge gold even in the
    light theme) and never re-renders on a toggle. When the reactive is passed
    it wins; the Solara hook stays as the fallback for a standalone render.

    The last step has no *Next*: Analysis is not a step, and the
    Evaluation step ends with its own *Start analysis*.
    """
    p = project.value
    with plan_once():  # the gates and badges below plan the active period once
        states = step_states(p)
        texts = [count_text(s, p) if states[i] is not StepStatus.LOCKED or i == active_step else ""
                 for i, s in enumerate(STEPS)]
    prev_t, next_t = nav_targets(active_step, states)
    menu_open, set_menu_open = solara.use_state(False)
    # The visit (a count of step changes) a locked-tab click happened on: the
    # note clears by itself on the next navigation, and stays cleared on a
    # return to the same step, with no effect to reset it.
    visit = solara.use_ref([active_step, 0])
    if visit.current[0] != active_step:
        visit.current = [active_step, visit.current[1] + 1]
    lock_note_at, set_lock_note_at = solara.use_state(None)
    active = STEPS[active_step]
    level_steps = steps_of(active.level)

    # Filled segments + the current-step ring use the app's theme "primary"
    # accent (green in light mode, gold in dark) — the same colour as the Next
    # button and every color="primary" control, so the strip matches the app.
    themes = solara.lab.theme.themes
    # The hook must be called unconditionally (rules of hooks) even when the
    # `dark` reactive overrides it.
    solara_dark = solara.lab.use_dark_effective()
    is_dark = solara_dark if dark is None else dark.value
    primary = themes.dark.primary if is_dark else themes.light.primary
    ring_css = f"box-shadow: 0 0 0 2px {_rgba(primary, 0.55)};"

    def _seg_style(i: int) -> str:
        """Style of the inner visual bar (the padded wrapper owns the click).
        An optional step is dashed, hollow until it has its own outputs (a
        passed-but-empty optional step is HAS_OUTPUTS, yet nothing was made)."""
        if states[i] is StepStatus.LOCKED:
            bg = f"background: {_SEG_LOCKED};"
        elif STEPS[i].optional:
            bg = f"background: {primary if STEPS[i].count(p) else 'transparent'};"
        else:
            fill = primary if states[i] is StepStatus.HAS_OUTPUTS else _SEG_EMPTY
            bg = f"background: {fill};"
        if STEPS[i].optional:
            bg += f" border: 1.5px dashed {primary}; box-sizing: border-box;"
        ring = ring_css if i == active_step else ""
        return f"width: 100%; height: 7px; border-radius: 3.5px; {bg}{ring}"

    def _open_level(level: str):
        """A tab opens its level's first unlocked step; a locked level only
        shows why, and the current level's own tab changes nothing."""
        first = next((i for i in steps_of(level) if states[i] is not StepStatus.LOCKED), None)
        if first is None:
            set_lock_note_at(visit.current[1])
        elif level != active.level:
            on_navigate(first)

    def _jump(i: int):
        """Navigate from the dropdown, then close it.

        Locked rows are inert twice over: Vuetify puts ``pointer-events: none``
        on a disabled list item, and this re-checks — because ``rv.use_event``
        is a hook, it must be attached to every row on every render, so the
        no-op has to live in the handler rather than around it.
        """
        if states[i] is StepStatus.LOCKED:
            return
        set_menu_open(False)
        on_navigate(i)

    with solara.Column(
        style="gap: 0px; padding: 10px 12px 6px;"
        " border-bottom: 1px solid rgba(128, 128, 128, 0.25);"
    ):
        # Both levels in one strip: per level, its label over its bars, the
        # groups as wide as their step counts. A level is ticked once its last
        # required step has outputs. Each cell is a padded transparent WRAPPER
        # (~21 px tall) carrying the tooltip and click handler — the 7 px bar
        # inside is purely visual, so the hit area meets the spec's >=20 px.
        solara.Style(
            ".defo-level-label:not(.defo-level-label--on):not([aria-disabled='true']):hover"
            " { text-decoration: underline; }"
        )
        with solara.Row(style="gap: 10px; align-items: flex-end; flex-wrap: nowrap;"):
            for level in LEVELS:
                idx = steps_of(level)
                locked = all(states[i] is StepStatus.LOCKED for i in idx)
                last_required = [i for i in idx if not STEPS[i].optional][-1]
                label = t(_LEVEL_LABELS[level])
                with solara.Column(gap="0px", style=f"flex: {len(idx)} 1 0; min-width: 0;"):
                    _LevelLabel(
                        level=level,
                        label=label,
                        tip=f"{label} · {t(STEPS[idx[0]].lock_reason_key)}" if locked else label,
                        done=states[last_required] is StepStatus.HAS_OUTPUTS,
                        active=level == active.level,
                        locked=locked,
                        primary=primary,
                        on_activate=lambda level=level: _open_level(level),
                    )
                    with solara.Row(style="gap: 4px; align-items: center; flex-wrap: nowrap;"):
                        for i in idx:
                            spec = STEPS[i]
                            cell_locked = states[i] is StepStatus.LOCKED
                            _SegmentCell(
                                key=spec.key,
                                label=t(spec.label_key),
                                tip=t(spec.lock_reason_key)
                                if cell_locked
                                else f"{t(spec.label_key)} · {texts[i]}",
                                seg_style=_seg_style(i),
                                locked=cell_locked,
                                current=i == active_step,
                                on_activate=lambda i=i: on_navigate(i),
                            )
        # Only Review can lock; the note shows on the step it was clicked from,
        # while Review is still locked.
        review = steps_of("review")
        if lock_note_at == visit.current[1] and all(states[i] is StepStatus.LOCKED for i in review):
            solara.Text(t(STEPS[review[0]].lock_reason_key), style=MUTED + "font-size: 11px; padding: 0 2px;")

        with solara.Row(style="gap: 8px; align-items: center; padding: 4px 0;"):
            solara.Button(
                icon_name="mdi-chevron-left",
                icon=True,
                small=True,
                disabled=prev_t is None,
                on_click=lambda: prev_t is not None and on_navigate(prev_t),
            )
            # Unified activator: badge + title + count + caret in ONE clickable
            # element that opens the jump menu (replaces the old two-line title
            # column and the separate "ALL STEPS" button). Borderless at rest;
            # the :hover tint needs real CSS, injected per-render so it tracks
            # the live theme primary.
            solara.Style(
                f".sr-step-jump:hover {{ background: {_rgba(primary, 0.10)}; }}"
                # The Row's actual flex child is the <v-menu> root div (reacton
                # claims the activator into the menu), and solara.lab.Menu's
                # style= lands on the menu CONTENT, not that root — so the
                # stretch needs this wrapper rule instead.
                " .sr-step-jump-wrap > .v-menu { width: 100%; }"
            )
            with rv.Html(
                tag="div",
                class_="sr-step-jump-wrap",
                style_="flex: 1 1 auto; min-width: 0;",
            ):
                with rv.Html(
                    tag="div",
                    class_="sr-step-jump",
                    style_="display: flex; align-items: center; gap: 8px;"
                    " width: 100%; padding: 5px 10px 5px 8px; border-radius: 6px;"
                    " cursor: pointer;",
                ) as activator:
                    # An optional step left empty reads "optional", not its
                    # place, and drops the chip (which would say it again).
                    empty_optional = active.optional and not active.count(p)
                    rv.Html(
                        tag="span",
                        class_="defo-step-badge",
                        children=[
                            t("workflow.optional")
                            if empty_optional
                            else t(
                                "workflow.step_badge",
                                n=level_steps.index(active_step) + 1,
                                total=len(level_steps),
                            )
                        ],
                        style_=f"background: {primary}; color: #ffffff;"
                        " font-weight: 600; font-size: 11px; border-radius: 4px;"
                        " padding: 2px 6px; white-space: nowrap;",
                    )
                    solara.Text(t(active.label_key), style="font-weight: 700;")
                    if not empty_optional:
                        rv.Chip(children=[texts[active_step]], x_small=True)
                    rv.Icon(
                        children=["mdi-menu-down"],
                        small=True,
                        style_="margin-left: auto;",
                    )
                # solara.lab.Menu renders `activator` into its v-menu activator slot
                # and wires the click in Vue (@click.native on the slot element — any
                # element works, verified against solara's menu.vue), so no
                # widget-level on_click is involved.
                with solara.lab.Menu(
                    activator=activator,
                    open_value=menu_open,
                    on_open_value=set_menu_open,
                    # Locked rows would otherwise dismiss the menu on click
                    # (Vuetify counts them as content); _jump closes it on a
                    # real jump instead.
                    close_on_content_click=False,
                    # Load-bearing — do NOT set this to False. It makes solara
                    # pass min-width="auto" to v-menu, and Vuetify's off-screen
                    # guard then computes max(content_width, parseFloat("auto"))
                    # = NaN, so its `overflow > 0` test is false and the menu is
                    # never pulled back into the viewport. A numeric min-width
                    # keeps the clamp working.
                    use_activator_width=True,
                ):
                    with rv.List(dense=True, style_="width: 100%; padding: 4px 0;"):
                        for n, i in enumerate(level_steps, start=1):
                            spec = STEPS[i]
                            locked = states[i] is StepStatus.LOCKED
                            _JumpMenuRow(
                                index=i,
                                number=n,
                                label=t(spec.label_key),
                                sub_text=t(spec.lock_reason_key)
                                if locked
                                else texts[i],
                                locked=locked,
                                active=i == active_step,
                                on_jump=_jump,
                            )
            solara.Button(
                icon_name="mdi-chevron-right",
                icon=True,
                small=True,
                color="primary",
                disabled=next_t is None,
                on_click=lambda: next_t is not None and on_navigate(next_t),
            )

        # The step's instruction, with its longer explanation behind the info
        # button (this replaced each step body's description line).
        with solara.Row(style="gap: 6px; align-items: center; flex-wrap: nowrap; padding: 0 2px;"):
            solara.Text(t(f"{active.text_key}.instruction"), style="flex: 1 1 auto; font-weight: 500;")
            InfoButton(t(f"{active.text_key}.info_header"), t(f"{active.text_key}.info_md"))
