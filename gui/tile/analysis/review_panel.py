"""Block B of the Analysis controls: the verdict.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Three
buttons write ``AnalysisState.verdict`` — the status string the store, the
PMTiles paint values and Overview's counts all read, never a label — the loss
driver disappears for a false positive (none is stored for one), and **Save &
next** is the panel's one primary button: it saves and advances only if the
save wrote (spec §4).

Nothing is stashed on ``widgets`` any more except ``driver_combobox`` (kept
for tests until the Questions section replaces it, Task 6). The combobox reads
and writes ``analysis.answers["loss_driver"]``, which ``state.capture_alert``
takes at the click (the save then writes the captured value — and writes none
at all for a false positive, which is why hiding the field here is not a lie).

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch; the driver combobox is stashed here
    (transitional, Task 6), but a Save reads ``analysis.answers``.
app_state : AppState
    Navigation + alerts source of truth; **Save & next** advances it.
analysis : AnalysisState
    The per-alert ``verdict`` / ``answers`` / ``dirty`` / ``last_save_time``
    this block reads and writes.
app_tile_model : SetContext
    The active set's folder, for the save runner this block builds.
run_save : callable or None
    The export section's ``run_save`` when the tile has one, so both Save
    buttons run the same save; ``None`` makes one here.

The block is built once per session (MapApp re-views it from its model on
reopen), so the reactive subscriptions below are registered once and their
unsubscribers are deliberately discarded.
"""

from datetime import datetime

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.project.models import default_questions
from gui.i18n import t
from gui.scripts import questions
from gui.tile.analysis.export import make_save_runner
from gui.tile.analysis.state import touch

#: The review statuses a Save stores, with the key of each one's button label.
#: The store always gets the status; the label follows the locale.
REVIEW_CHOICES = (
    ("Confirmed", "analysis_tile.verdict.true"),
    ("False Positive", "analysis_tile.verdict.false"),
    ("Need revision", "analysis_tile.verdict.revision"),
)

#: status -> the icon on its button
_ICONS = {
    "Confirmed": "mdi-check-bold",
    "False Positive": "mdi-close-thick",
    "Need revision": "mdi-flag-outline",
}

_SLOTS = "_defo_verdict_slots"
_HIDDEN = "display: none;"


def verdict_widgets(card):
    """``(buttons, driver_box, note, save_next, save, state_line)``."""
    s = getattr(card, _SLOTS)
    return (s["buttons"], s["driver_box"], s["note"], s["save_next"], s["save"], s["state_line"])


def build_review_panel(widgets, app_state, analysis, app_tile_model, run_save=None):
    """Build and return the verdict card.

    ``run_save`` is the export section's runner when the tile has one, so a
    save from here runs the same post-save step and the export section's build
    button lights up; without it the block makes its own.
    """
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

    # Codes as values, labels as text: the store gets the codes whatever the
    # language the widgets were built in. Objects in and out, as Vuetify 2's
    # combobox draws a chip from the selected value itself.
    #
    # Transitional until the Questions section replaces it (Task 6): the
    # combobox is a view of ``analysis.answers["loss_driver"]``, which a Save
    # reads (``capture_alert``). It opens empty (spec U13: no default).
    _loss = default_questions()[0]
    driver_combobox = sw.Combobox(
        items=questions.items(_loss),
        v_model=questions.as_items(_loss, analysis.answers.value.get(_loss.key, [])),
        return_object=True,
        label=t("analysis_tile.questionarie.loss_driver_hint"),
        multiple=True,
        clearable=True,
        chips=True,
    )

    def _driver_change(*_):
        analysis.answers.set({**analysis.answers.value,
                              _loss.key: questions.codes(_loss, questions.encode(_loss, driver_combobox.v_model))})
        touch(analysis)

    driver_combobox.on_event("change", _driver_change)
    analysis.answers.subscribe(lambda *_: setattr(
        driver_combobox, "v_model", questions.as_items(_loss, analysis.answers.value.get(_loss.key, []))))
    widgets.driver_combobox = driver_combobox

    driver_box = v.Html(tag="div", class_="mt-3", children=[
        v.Html(tag="div", class_="body-2", children=[t("analysis_tile.questionarie.loss_driver_label")]),
        driver_combobox,
    ])
    note = v.Html(tag="div", class_="mt-3 pa-2 caption text--secondary",
                  style_=_HIDDEN, children=[t("analysis_tile.verdict.no_driver")])

    save_next = v.Btn(color="primary", class_="flex-grow-1", style_="height: 44px;",
                      children=[t("analysis_tile.verdict.save_next"),
                                v.Icon(right=True, children=["mdi-arrow-right"])])
    save = v.Btn(outlined=True, style_="height: 44px;",
                 children=[t("analysis_tile.export_labels.save_btn")])
    state_line = v.Html(tag="div", class_="caption mt-2", children=[])

    if run_save is None:
        run_save = make_save_runner(widgets, app_state, analysis, app_tile_model)
    save.on_event("click", lambda *_: run_save(save))
    save_next.on_event("click", lambda *_: run_save(save_next, then=app_state.go_next))
    # Both buttons are the same save: the runner holds them together for its
    # duration, so a save started from one cannot be raced by the other over
    # the same store and row. ``getattr`` because a test may inject a plain
    # callable as ``run_save``.
    guard = getattr(run_save, "guard", None)
    if guard is not None:
        guard(save_next, save)

    def _sync_verdict(*_):
        chosen = analysis.verdict.value
        for status, btn in buttons.items():
            btn.outlined = status != chosen
        is_false = chosen == "False Positive"
        driver_box.style_ = _HIDDEN if is_false else ""
        note.style_ = "" if is_false else _HIDDEN

    def _sync_state_line(*_):
        if analysis.drawing_unapplied.value:
            # The save refuses an unapplied drawing
            # (``export._save_attributes``), so the line says what to do about
            # it rather than leaving the refusal to the click. Ahead of
            # "Unsaved changes": applying is what clears both.
            state_line.children = [t("analysis_tile.edition_labels.unapplied_drawing")]
            state_line.class_ = "caption mt-2 warning--text"
            return
        if analysis.dirty.value:
            state_line.children = [t("analysis_tile.verdict.unsaved")]
            state_line.class_ = "caption mt-2 warning--text"
            return
        when = analysis.last_save_time.value
        if when:
            state_line.children = [t("analysis_tile.verdict.saved_at",
                                     time=datetime.fromtimestamp(when).strftime("%H:%M"))]
            state_line.class_ = "caption mt-2 success--text"
        else:
            state_line.children = []
            state_line.class_ = "caption mt-2"

    # Registered once: the panel is built once per session.
    analysis.verdict.subscribe(_sync_verdict)
    analysis.dirty.subscribe(_sync_state_line)
    analysis.drawing_unapplied.subscribe(_sync_state_line)
    analysis.last_save_time.subscribe(_sync_state_line)
    _sync_verdict()
    _sync_state_line()

    card = v.Card(class_="pa-3 ma-2", hover=True, children=[
        v.Html(tag="div", class_="subtitle-2 font-weight-bold", children=[t("analysis_tile.verdict.title")]),
        v.Html(tag="div", class_="mt-2", children=[group]),
        driver_box,
        note,
        v.Html(tag="div", class_="d-flex mt-3", style_="gap: 8px;", children=[save_next, save]),
        state_line,
    ])
    setattr(card, _SLOTS, {"buttons": buttons, "driver_box": driver_box, "note": note,
                           "save_next": save_next, "save": save, "state_line": state_line})
    return card


def _pick(analysis, status):
    # ``dirty`` follows a verdict *change* (spec §4.4): ``verdict.set`` on an
    # equal value notifies nobody, so pressing the already-selected answer
    # after a save must not flip the line to "Unsaved changes" either.
    if analysis.verdict.value == status:
        return
    analysis.verdict.set(status)
    touch(analysis)
