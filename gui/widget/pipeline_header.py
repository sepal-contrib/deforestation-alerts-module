"""Compact pipeline header for the workflow panel.

One title row (a step badge counting all steps, the active step's name, its
output count and a caret) that opens the jump menu, the step's instruction
line with its info button, and a footer with Back / Next. The jump menu lists
every step under its level's heading (Prepare alerts | Set up review), each
with a numbered dot that tells its state. ``STEPS`` stays one flat list:
Back / Next cross the level boundary.

The footer is rendered here, with the header, so one render plans the steps'
gates once; the shell's stylesheet pins it to the bottom of the panel
(``.defo-workflow-footer``).

Semantics are outputs, not completion — steps are revisitable workspaces,
never "done". Dot/badge data all derives from the STEPS registry.
"""

import reacton.ipyvuetify as rv
import solara
import solara.lab

from gui.i18n import t
from gui.store.workflow_steps import LEVELS, STEPS, StepStatus, nav_targets, plan_once, step_states, steps_of
from gui.widget.help import InfoButton
from gui.widget.text_style import MUTED

_LEVEL_LABELS = {"prepare": "workflow.level_prepare", "review": "workflow.level_review"}
_GREY = "rgba(128, 128, 128, 0.6)"


def _rgba(hex_color: str, alpha: float) -> str:
    """`#rrggbb` -> `rgba(r, g, b, alpha)` (for the translucent hover tint)."""
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


def dot_style(spec, state: StepStatus, p, primary: str) -> str:
    """A menu row's numbered dot: filled when the step has outputs, a grey ring
    when empty, dashed for an optional step (filled only once it made
    something of its own: a passed-but-empty optional step is HAS_OUTPUTS)."""
    if spec.optional:
        filled = bool(spec.count(p))
        ring = f"1.5px dashed {primary}"
    else:
        filled = state is StepStatus.HAS_OUTPUTS
        ring = f"1.5px solid {primary if filled else _GREY}"
    look = f"background: {primary}; color: #ffffff;" if filled else f"color: {primary if spec.optional else 'inherit'};"
    return (f"{look} border: {ring}; box-sizing: border-box; width: 20px; height: 20px; border-radius: 50%;"
            " display: inline-flex; align-items: center; justify-content: center; flex: none;"
            " font-size: 11px; font-weight: 700; margin-right: 10px;")


@solara.component
def _JumpMenuRow(index: int, key: str, label: str, sub_text: str, dot: str, locked: bool, active: bool, on_jump):
    """One row of the step-jump dropdown.

    Its own component so the ``rv.use_event`` click hook is called exactly once
    at this component's top level — never inside the caller's loop over STEPS
    (rules of hooks). ``on_jump`` re-checks locked itself — Vuetify's
    ``pointer-events: none`` on a disabled item is only the first guard.
    ``data-step-key`` lets the headless driver click a step by key.
    """
    with rv.ListItem(
        disabled=locked,
        link=not locked,
        color="primary",
        input_value=active,
        attributes={
            "data-step-key": key, "title": f"{label} · {sub_text}",
            "aria-current": "step" if active else "false", "aria-disabled": "true" if locked else "false",
        },
    ) as row:
        if locked:
            rv.Icon(children=["mdi-lock-outline"], small=True, style_="width: 20px; margin-right: 10px;")
        else:
            rv.Html(tag="span", class_="defo-step-dot", children=[str(index + 1)], style_=dot)
        with rv.ListItemContent():
            rv.ListItemTitle(children=[label])
        solara.Text(sub_text, style=MUTED + "font-size: 11px; margin-left: 12px; text-align: right;")
    rv.use_event(row, "click", lambda *_: on_jump(index))


@solara.component
def PipelineHeader(active_step: int, on_navigate, project, dark=None):
    """Step title + jump menu + Back / Next footer.

    Takes the ``project`` reactive (not its value — reacton's prop-equality
    bailout would eat project-only changes) plus the active index and a
    navigate callback.

    ``dark`` is an optional reactive mirroring pysepal's ``ThemeState.dark``.
    This app's theme is owned by ``theme_state``/``MapApp``, NOT by Solara's own
    dark mode, so ``solara.lab.use_dark_effective()`` reports the wrong value
    here (it is always True, which painted the badge gold even in the light
    theme) and never re-renders on a toggle. When the reactive is passed it
    wins; the Solara hook stays as the fallback for a standalone render.

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
    active = STEPS[active_step]

    # The badge, the dots and the hover tint use the app's theme "primary"
    # accent (green in light mode, gold in dark), as every color="primary" control.
    themes = solara.lab.theme.themes
    # The hook must be called unconditionally (rules of hooks) even when the
    # `dark` reactive overrides it.
    solara_dark = solara.lab.use_dark_effective()
    is_dark = solara_dark if dark is None else dark.value
    primary = themes.dark.primary if is_dark else themes.light.primary

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

    with solara.Column(gap="0px"):  # one root: the header, then the footer the stylesheet pins
        with solara.Column(
            style="gap: 0px; padding: 8px 12px 6px;"
            " border-bottom: 1px solid rgba(128, 128, 128, 0.25);"
        ):
            with solara.Row(style="gap: 8px; align-items: center; padding: 4px 0;"):
                # Unified activator: badge + title + count + caret in ONE clickable
                # element that opens the jump menu. Borderless at rest; the :hover
                # tint needs real CSS, injected per-render so it tracks the live
                # theme primary.
                solara.Style(
                    f".sr-step-jump:hover {{ background: {_rgba(primary, 0.10)}; }}"
                    # The Row's actual flex child is the <v-menu> root div (reacton
                    # claims the activator into the menu), and solara.lab.Menu's
                    # style= lands on the menu CONTENT, not that root — so the
                    # stretch needs this wrapper rule instead.
                    " .sr-step-jump-wrap > .v-menu { width: 100%; }"
                )
                with rv.Html(tag="div", class_="sr-step-jump-wrap", style_="flex: 1 1 auto; min-width: 0;"):
                    with rv.Html(
                        tag="div",
                        class_="sr-step-jump",
                        style_="display: flex; align-items: center; gap: 8px;"
                        " width: 100%; padding: 5px 10px 5px 8px; border-radius: 6px;"
                        " cursor: pointer;",
                        # data-active-step: the headless driver reads the current step off it.
                        attributes={"data-active-step": active.key, "title": f"{t(active.label_key)} · {texts[active_step]}"},
                    ) as activator:
                        # An optional step left empty shows no count chip: its
                        # dashed dot already marks it optional.
                        empty_optional = active.optional and not active.count(p)
                        rv.Html(
                            tag="span",
                            class_="defo-step-badge",
                            children=[t("workflow.step_badge", n=active_step + 1, total=len(STEPS))],
                            style_=f"background: {primary}; color: #ffffff;"
                            " font-weight: 600; font-size: 11px; border-radius: 4px;"
                            " padding: 2px 6px; white-space: nowrap;",
                        )
                        solara.Text(t(active.label_key), style="font-weight: 700;")
                        if not empty_optional:
                            rv.Chip(children=[texts[active_step]], x_small=True)
                        rv.Icon(children=["mdi-menu-down"], small=True, style_="margin-left: auto;")
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
                            for level in LEVELS:
                                idx = steps_of(level)
                                # A level is ticked once its last required step has outputs.
                                last_required = [i for i in idx if not STEPS[i].optional][-1]
                                done = states[last_required] is StepStatus.HAS_OUTPUTS
                                rv.Subheader(
                                    children=[("✓ " if done else "") + t(_LEVEL_LABELS[level])],
                                    attributes={"data-level-key": level},
                                    style_="height: 28px; font-size: 11px; font-weight: 700;"
                                    " text-transform: uppercase; letter-spacing: 0.04em;"
                                    + (f" color: {primary};" if done else ""),
                                )
                                for i in idx:
                                    spec = STEPS[i]
                                    locked = states[i] is StepStatus.LOCKED
                                    _JumpMenuRow(
                                        index=i,
                                        key=spec.key,
                                        label=t(spec.label_key),
                                        sub_text=t(spec.lock_reason_key) if locked else texts[i],
                                        dot=dot_style(spec, states[i], p, primary),
                                        locked=locked,
                                        active=i == active_step,
                                        on_jump=_jump,
                                    )

            # The step's instruction, with its longer explanation behind the info
            # button (this replaced each step body's description line).
            with solara.Row(style="gap: 6px; align-items: center; flex-wrap: nowrap; padding: 0 2px;"):
                solara.Text(t(f"{active.text_key}.instruction"), style="flex: 1 1 auto; font-weight: 500;")
                InfoButton(t(f"{active.text_key}.info_header"), t(f"{active.text_key}.info_md"))

        # Back / Next: fixed labels, so the buttons never grow with a long step
        # name; the target's name is the tooltip. Back skips locked steps, as Next.
        with rv.Html(tag="div", class_="defo-workflow-footer"):
            if prev_t is None:
                rv.Html(tag="span")  # keeps Next on the right
            else:
                solara.Button(
                    t("workflow.back"),
                    icon_name="mdi-chevron-left",
                    text=True,
                    on_click=lambda: on_navigate(prev_t),
                    attributes={"data-nav": "back", "title": t(STEPS[prev_t].label_key)},
                )
            if next_t is not None:
                solara.Button(
                    children=[t("workflow.next"), rv.Icon(children=["mdi-chevron-right"], right=True)],
                    color="primary",
                    depressed=True,
                    on_click=lambda: on_navigate(next_t),
                    attributes={"data-nav": "next", "title": t(STEPS[next_t].label_key)},
                )
