"""Block B of the Analysis controls: the verdict.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Three
buttons write ``AnalysisState.verdict`` — the status string the store, the
PMTiles paint values and Overview's counts all read, never a label.

**Save & next**, **Save** and the save-state line moved to the panel's sticky
footer (``footer``, spec D6), under the Export section, so the card holds the
verdict alone.

The loss driver moved to the Questions section (``questions_panel``, spec D4),
with every other question of the project: the answers are
``analysis.answers``, which ``state.capture_alert`` takes at the click.
Nothing is stashed on ``widgets``.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch (unused here; kept for the builders'
    common call shape).
app_state : AppState
    Navigation + alerts source of truth.
analysis : AnalysisState
    The per-alert ``verdict`` this block reads and writes, and ``dirty``,
    which a verdict change sets.

The block is built once per session (MapApp re-views it from its model on
reopen), so the reactive subscriptions below are registered once and their
unsubscribers are deliberately discarded.
"""

import ipyvuetify as v

from deforestation_alerts.project.review_status import (
    CONFIRMED, FALSE_POSITIVE, NEED_REVISION, VERDICT_LABEL_KEYS, VERDICTS,
)
from gui.i18n import t
from gui.tile.analysis.state import touch
from gui.tile.analysis.section import TYPE_TITLE, panel_block

#: The review statuses a Save stores, with the key of each one's button label.
#: The store always gets the status; the label follows the locale.
REVIEW_CHOICES = tuple((status, VERDICT_LABEL_KEYS[status]) for status in VERDICTS)

#: status -> the icon on its button
_ICONS = {
    CONFIRMED: "mdi-check-bold",
    FALSE_POSITIVE: "mdi-close-thick",
    NEED_REVISION: "mdi-flag-outline",
}

_SLOTS = "_defo_verdict_slots"


def verdict_widgets(card):
    """The verdict buttons, ``{status: v.Btn}``."""
    return getattr(card, _SLOTS)["buttons"]


def build_review_panel(widgets, app_state, analysis):
    """Build and return the verdict card: its title and the three buttons."""
    buttons = {}
    for status, key in REVIEW_CHOICES:
        btn = v.Btn(
            outlined=True, color="primary", class_="flex-grow-1 pa-1",
            style_="height: 58px; text-transform: none;",
            children=[v.Icon(small=True, children=[_ICONS[status]]),
                      v.Html(tag="span", class_="caption ml-1", children=[t(key)])],
        )
        btn.on_event("click", lambda *_, status=status: _pick(analysis, status))
        buttons[status] = btn

    group = v.Html(
        tag="div", class_="d-flex", style_="gap: 6px;",
        attributes={"role": "group",
                    "aria-label": t("analysis_tile.questionarie.confirmation_q")},
        children=list(buttons.values()),
    )

    def _sync_verdict(*_):
        chosen = analysis.verdict.value
        for status, btn in buttons.items():
            btn.outlined = status != chosen

    # Registered once: the panel is built once per session.
    analysis.verdict.subscribe(_sync_verdict)
    _sync_verdict()

    card = panel_block([
        v.Html(tag="div", class_=TYPE_TITLE, children=[t("analysis_tile.verdict.title")]),
        v.Html(tag="div", class_="mt-2", children=[group]),
    ])
    setattr(card, _SLOTS, {"buttons": buttons})
    return card


def _pick(analysis, status):
    # ``dirty`` follows a verdict *change* (spec §4.4): ``verdict.set`` on an
    # equal value notifies nobody, so pressing the already-selected answer
    # after a save must not flip the line to "Unsaved changes" either.
    if analysis.verdict.value == status:
        return
    analysis.verdict.set(status)
    touch(analysis)
