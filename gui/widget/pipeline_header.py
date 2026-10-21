"""Compact pipeline header for the workflow panel.

Replaces the overflowing ``rv.Tabs`` strip: a segment strip (one cell per STEPS entry) mapping the
pipeline (has-outputs / empty / locked, with a ring on the current step), a
title row with the active step's name, output count and position, a unified
clickable title (badge + name + count + caret) that opens the jump menu, and
Back / Next buttons.

Semantics are outputs, not completion — steps are revisitable workspaces,
never "done". Segment/badge data all derives from the STEPS registry.
"""

import reacton.ipyvuetify as rv
import solara
import solara.lab

from gui.i18n import t
from gui.store.workflow_steps import STEPS, StepStatus, nav_targets, step_states
from gui.widget.text_style import MUTED

# Empty / locked tones stay theme-neutral grey; the HAS_OUTPUTS fill and the
# current-step ring derive from the live Vuetify "primary" colour at render
# time (see PipelineHeader) so the strip matches the app accent in both modes.
_SEG_EMPTY = "rgba(128, 128, 128, 0.28)"
_SEG_LOCKED = (
    "repeating-linear-gradient(90deg, rgba(128,128,128,0.30) 0 3px,"
    " rgba(128,128,128,0.10) 3px 6px)"
)


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


@solara.component
def _SegmentCell(key: str, tip: str, seg_style: str, locked: bool, on_activate):
    """One segment of the pipeline strip.

    Its own component so the ``rv.use_event`` click hook is called exactly once
    at this component's top level — never inside the caller's loop over STEPS
    (rules of hooks). Locked cells keep the handler attached and no-op inside
    it, so lock/unlock never changes the hook count. ``data-step-key`` lets the
    headless driver click a step by key.
    """
    cursor = "" if locked else " cursor: pointer;"
    with rv.Html(
        tag="div",
        style_=f"flex: 1; padding: 7px 0;{cursor}",
        attributes={"title": tip, "data-step-key": key},
    ) as cell:
        rv.Html(tag="div", style_=seg_style)
    rv.use_event(cell, "click", lambda *_: not locked and on_activate())


@solara.component
def _JumpMenuRow(
    index: int, label: str, sub_text: str, locked: bool, active: bool, on_jump
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
                str(index + 1),
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
    states = step_states(p)
    prev_t, next_t = nav_targets(active_step, states)
    menu_open, set_menu_open = solara.use_state(False)

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
        """Style of the inner visual bar (the padded wrapper owns the click)."""
        if states[i] is StepStatus.LOCKED:
            bg = f"background: {_SEG_LOCKED};"
        else:
            fill = primary if states[i] is StepStatus.HAS_OUTPUTS else _SEG_EMPTY
            bg = f"background: {fill};"
        ring = ring_css if i == active_step else ""
        return f"width: 100%; height: 7px; border-radius: 3.5px; {bg}{ring}"

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
        # Segment strip. Each cell is a padded transparent WRAPPER (~21 px
        # tall) carrying the tooltip and click handler — the 7 px bar inside
        # is purely visual, so the hit area meets the spec's >=20 px.
        with solara.Row(style="gap: 4px; align-items: center;"):
            for i, spec in enumerate(STEPS):
                locked = states[i] is StepStatus.LOCKED
                _SegmentCell(
                    key=spec.key,
                    tip=t(spec.lock_reason_key)
                    if locked
                    else f"{t(spec.label_key)} · {count_text(spec, p)}",
                    seg_style=_seg_style(i),
                    locked=locked,
                    on_activate=lambda i=i: on_navigate(i),
                )

        active = STEPS[active_step]
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
                    rv.Html(
                        tag="span",
                        children=[
                            t(
                                "workflow.step_badge",
                                n=active_step + 1,
                                total=len(STEPS),
                            )
                        ],
                        style_=f"background: {primary}; color: #ffffff;"
                        " font-weight: 600; font-size: 11px; border-radius: 4px;"
                        " padding: 2px 6px; white-space: nowrap;",
                    )
                    solara.Text(t(active.label_key), style="font-weight: 700;")
                    rv.Chip(children=[count_text(active, p)], x_small=True)
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
                        for i, spec in enumerate(STEPS):
                            locked = states[i] is StepStatus.LOCKED
                            _JumpMenuRow(
                                index=i,
                                label=t(spec.label_key),
                                sub_text=t(spec.lock_reason_key)
                                if locked
                                else count_text(spec, p),
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
