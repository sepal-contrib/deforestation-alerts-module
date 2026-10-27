"""The Analysis panel's dock (user, 2026-10-26, option B): the Export
section, then *Save* / *Next*, then the save-state line, fixed to the bottom
edge of the panel -- a sibling of the scrolling area (``build_screen``), so it
never floats halfway up when the sections above are closed, and the review
rhythm (answer, Save, Next) never needs a scroll, however many questions the
project asks. The Export header is the dock's first line: closed, its chevron
points up, where its list opens.

Architecture layer: view (raw-widget builder, no ``@solara.component``),
built once per session with the rest of the panel; the reactive subscriptions
below are registered once and their unsubscribers deliberately discarded.

**Save** and **Next** are two buttons. The filled one moves: *Save* while the
alert has unsaved work or no saved review, *Next* once its review is saved.
*Save* drives the export section's ``run_save`` -- there is one save runner
in the panel -- and is registered with it (``run_save.guard``) so a second
save cannot start over the same store and row.

**Next with unsaved work** asks first (``guard``): the button row gives way to
an inline prompt, *Discard* (move on; the alert opens again from its store)
or *Stay*. Every alert change started on the screen goes through the same
``guard``: this *Next*, and the top bar's Previous, Next, Go and next
unreviewed (``context.build_context``).

Flat on the panel's surface, like the blocks above it: the same 16 px edges
and the same hairline, no card of its own. The background is opaque; the
inline colour is the light theme's (``--v-background-base`` is not themed, so
its fallback is what renders); the dark theme's is restated in the shell's
stylesheet (``solara_app._AppShell``: ``.theme--dark .defo-analysis-footer``).
"""

import logging
from datetime import datetime
from types import SimpleNamespace

import ipyvuetify as v

from deforestation_alerts.project import review_groups
from gui.i18n import t, tn
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier
from gui.tile.analysis import alert_files
from gui.tile.analysis.context import status_label_key
from gui.tile.analysis.labels import Labels
from gui.tile.analysis.section import TYPE_BODY, TYPE_LABEL, TYPE_TITLE
from gui.widget.buttons import btn, set_label

FOOTER_STYLE = ("flex: 0 0 auto; padding: 0 16px 12px; "
                "background-color: var(--v-background-base, #ffffff); "
                "border-top: 1px solid rgba(128, 128, 128, 0.25);")
#: The unsaved-work prompt: the warning badge's orange, as a light panel.
PROMPT_STYLE = ("border: 1px solid rgba(239, 138, 12, 0.55); background-color: rgba(239, 138, 12, 0.07); "
                "border-radius: 6px; padding: 8px 10px 10px;")
_HIDDEN = "display: none;"
_SLOTS = "_defo_footer_slots"

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")


def footer_widgets(root):
    """``(save, next, state_line)`` of a footer ``build_footer`` built."""
    s = getattr(root, _SLOTS)
    return s["save"], s["next"], s["state_line"]


def prompt_widgets(root):
    """``(prompt, discard, stay)``: the unsaved-work prompt of a footer
    ``build_footer`` built, and its two buttons."""
    s = getattr(root, _SLOTS)
    return s["prompt"], s["discard"], s["stay"]


#: what the confirmation says of each kind of row a group save changes
_AFFECTED_KEYS = {
    review_groups.OVERWRITTEN: "analysis_tile.nearby.confirm_overwritten",
    review_groups.MOVED: "analysis_tile.nearby.confirm_moved",
    review_groups.JOINED: "analysis_tile.nearby.confirm_joined",
    review_groups.REMOVED: "analysis_tile.nearby.confirm_removed",
}


def affected_line(a, gdf) -> str:
    """The confirmation's line for one ``review_groups.Affected`` row of the
    plan ``gdf`` was planned on: its number, its stored status and what the
    save does to it ("its review is replaced", "moves from group #K"...).
    Alert and group numbers are 1-based, as the jump menu numbers them (the
    group's is its main's). ``gdf`` is the table the plan was made from; the
    status the plan recorded is the one shown."""
    del gdf  # the plan carries what the line needs
    group = a.group + 1 if a.group is not None else None
    return t(_AFFECTED_KEYS[a.change], n=a.position + 1, status=t(status_label_key(a.status)), group=group)


_CONFIRM_TITLE, _CONFIRM_OK = "analysis_tile.nearby.confirm_title", "analysis_tile.nearby.confirm_ok"


def _build_confirm(labels):
    """The group save's confirmation: a raw ``v.Dialog``, like the *Stop
    drawing* one in ``edit_tools`` (the panel never renders through reacton,
    so the Solara ``ConfirmDialog`` cannot be used here). ``ask(lines,
    on_ok, title=..., ok=...)`` lists ``lines`` under the ``title`` message
    key and opens it; the ``ok`` key's button (*Save* by default) closes it
    and runs ``on_ok``, *Cancel* only closes it. *Remove from group* asks
    with its own title and *Remove*."""
    ok = btn("primary", t(_CONFIRM_OK))
    cancel = btn("tertiary", t("analysis_tile.nearby.confirm_cancel"))
    title = v.CardTitle(style_="word-break: normal; font-size: 16px; font-weight: 600;",
                        children=[t(_CONFIRM_TITLE)])
    body = v.CardText(style_="font-size: 14px;", children=[])
    dialog = v.Dialog(v_model=False, max_width="480px", children=[
        v.Card(children=[title, body,
                         v.CardActions(class_="flex-wrap justify-end pa-4 pt-0", children=[cancel, ok])]),
    ])
    pending = {"on_ok": None}
    keys = {"title": _CONFIRM_TITLE, "ok": _CONFIRM_OK}

    def ask(lines, on_ok, *, title=_CONFIRM_TITLE, ok=_CONFIRM_OK):
        pending["on_ok"] = on_ok
        keys.update(title=title, ok=ok)
        _label()
        body.children = [v.Html(tag="div", class_=TYPE_BODY, children=[line]) for line in lines]
        dialog.v_model = True

    def _close():
        dialog.v_model = False
        pending["on_ok"] = None

    def _ok(*_):
        on_ok = pending["on_ok"]
        _close()
        if on_ok is not None:
            on_ok()

    ok.on_event("click", _ok)
    cancel.on_event("click", lambda *_: _close())

    def _label():
        set_label(ok, t(keys["ok"]))
        set_label(cancel, t("analysis_tile.nearby.confirm_cancel"))
        title.children = [t(keys["title"])]

    labels.add(_label)
    return SimpleNamespace(dialog=dialog, ask=ask, ok=ok, cancel=cancel, body=body)


def build_footer(run_save, export_root, app_state, analysis, *, labels=None, capture=None):
    """Build and return ``SimpleNamespace(root, save, next, state_line,
    prompt, guard, confirm)``.

    ``run_save`` is the export section's runner (``export.make_save_runner``),
    ``export_root`` the Export section, placed first in the dock.
    ``labels`` is the screen's ``labels.Labels`` (a fresh one when none is
    given): the buttons' words and the state line follow a language switch.
    ``guard(move)`` runs ``move`` (an alert change) at once, or after the
    reviewer's *Discard* when the alert has unsaved work.

    ``capture()`` (group review, spec 2026-10-26 §6.4) returns the alert as
    a Save writes it (``state.capture_alert``): with one, *Save* captures at
    the click and, when the plan changes rows beyond a first review
    (``plan.affected``), asks first in ``confirm`` (``_build_confirm``),
    listing ``affected_line`` for each; the save then writes that capture.
    It also words the button "Save for N alerts" while the plan holds more
    than one alert. Without it *Save* runs ``run_save(save)`` as before.
    """
    labels = labels if labels is not None else Labels()
    # The screen's size (``small``), and halves of the row, as the verdict
    # buttons above them. Which one is filled is ``_sync_emphasis``'s call.
    save = btn("primary", t("analysis_tile.export_labels.save_btn"), icon="mdi-content-save-outline",
               class_="flex-grow-1", style_="flex-basis: 0;")
    nxt = btn("secondary", t("analysis_tile.nav.next"), icon_right="mdi-arrow-right",
              class_="flex-grow-1", style_="flex-basis: 0;")
    buttons = v.Html(tag="div", class_="d-flex", style_="gap: 8px;", children=[save, nxt])
    state_line = v.Html(tag="div", class_="dat-caption mt-2", children=[])

    # The prompt, in place of the button row while it asks. *Stay* is the
    # filled one: the safe answer, and the one Enter would mean.
    prompt_title = v.Html(tag="span", class_=TYPE_TITLE, children=[""])
    prompt_hint = v.Html(tag="div", class_=TYPE_LABEL, style_="margin: 2px 0 8px 26px;", children=[""])
    discard = btn("secondary", "", icon="mdi-delete-outline", color="warning",
                  class_="flex-grow-1", style_="flex-basis: 0;")
    stay = btn("primary", "", class_="flex-grow-1", style_="flex-basis: 0;")
    prompt = v.Html(tag="div", class_="defo-unsaved-prompt", style_=PROMPT_STYLE + _HIDDEN, children=[
        v.Html(tag="div", class_="d-flex align-center warning--text", style_="gap: 6px;", children=[
            v.Icon(small=True, color="warning", children=["mdi-alert-outline"]), prompt_title]),
        prompt_hint,
        v.Html(tag="div", class_="d-flex", style_="gap: 8px;", children=[discard, stay]),
    ])

    # The alert change the prompt holds, run by *Discard*.
    pending = {"move": None}

    def _show_prompt(flag):
        prompt.style_ = PROMPT_STYLE + ("" if flag else _HIDDEN)
        # by class, not style: Vuetify's ``.d-flex`` is ``display: flex
        # !important``, which an inline ``display: none`` does not beat
        buttons.class_ = "d-none" if flag else "d-flex"
        if not flag:
            pending["move"] = None

    def guard(move):
        """Run ``move`` now, or ask first when the alert has unsaved work."""
        if not analysis.dirty.value:
            move()
            return
        pending["move"] = move
        _show_prompt(True)

    def _discard(*_):
        move = pending["move"]
        _show_prompt(False)
        # Nothing to undo by hand: the alert change re-seeds the review from
        # the store (``reset_alert`` and the per-alert load).
        if move is not None:
            move()

    confirm = _build_confirm(labels)

    def _on_save(*_):
        if capture is None:
            run_save(save)
            return
        try:
            alert = capture()
        except Exception as exc:
            # Said, not swallowed: the reviewer pressed Save and nothing ran.
            logger.exception("Analysis: reading the alert for the save failed")
            current_notifier().error(t("analysis_tile.export_labels.save_capture_failed", error=error_text(exc)))
            return
        plan = getattr(alert, "plan", None)
        if plan is not None and plan.affected:
            lines = [affected_line(a, alert.alerts) for a in plan.affected]
            confirm.ask(lines, lambda: run_save(save, alert=alert))
            return
        run_save(save, alert=alert)

    def _save_text():
        """"Save", or "Save for N alerts" while the plan on screen holds N > 1."""
        plan = None
        if capture is not None:
            try:
                plan = getattr(capture(), "plan", None)
            except Exception:  # a table in transition: the plain word
                plan = None
        if plan is not None and plan.size > 1:
            return tn("analysis_tile.export_labels.save_for", plan.size)
        return t("analysis_tile.export_labels.save_btn")

    save.on_event("click", _on_save)
    nxt.on_event("click", lambda *_: guard(app_state.go_next))
    discard.on_event("click", _discard)
    stay.on_event("click", lambda *_: _show_prompt(False))
    # ``getattr`` because a test may inject a plain callable.
    run_guard = getattr(run_save, "guard", None)
    if run_guard is not None:
        run_guard(save)

    # The question is about one alert's work: an alert change (however it
    # came) or a save that leaves nothing unsaved answers it.
    analysis.alert_token.subscribe(lambda *_: _show_prompt(False))
    analysis.dirty.subscribe(lambda dirty: None if dirty else _show_prompt(False))

    def _saved_review():
        """A save of this alert landed, this session or before (its row has a
        status)."""
        if analysis.last_save_time.value:
            return True
        return alert_files.saved_status(app_state.alerts_gdf.value, app_state.actual_alert_id.value) is not None

    def _sync_emphasis(*_):
        # Save is the next step while there is something to save; once the
        # review is stored, Next is.
        filled_save = bool(analysis.dirty.value) or not _saved_review()
        save.outlined = not filled_save
        nxt.outlined = filled_save

    for reactive in (analysis.dirty, analysis.last_save_time, app_state.alerts_gdf, app_state.actual_alert_id):
        reactive.subscribe(_sync_emphasis)
    _sync_emphasis()

    def _sync_state_line(*_):
        if analysis.drawing_unapplied.value:
            # The save refuses an unapplied drawing
            # (``review_save.save_review``), so the line says what to do about
            # it rather than leaving the refusal to the click. Ahead of
            # "Unsaved changes": applying is what clears both.
            state_line.children = [t("analysis_tile.edition_labels.unapplied_drawing")]
            state_line.class_ = "dat-caption mt-2 warning--text"
            return
        if analysis.dirty.value:
            state_line.children = [t("analysis_tile.verdict.unsaved")]
            state_line.class_ = "dat-caption mt-2 warning--text"
            return
        when = analysis.last_save_time.value
        if when:
            state_line.children = [t("analysis_tile.verdict.saved_at",
                                     time=datetime.fromtimestamp(when).strftime("%H:%M"))]
            state_line.class_ = "dat-caption mt-2 success--text"
        else:
            state_line.children = []
            state_line.class_ = "dat-caption mt-2"

    # Registered once: the panel is built once per session.
    analysis.dirty.subscribe(_sync_state_line)
    analysis.drawing_unapplied.subscribe(_sync_state_line)
    analysis.last_save_time.subscribe(_sync_state_line)
    # in place of a direct first call: ``add`` runs it now, and on a switch
    labels.add(_sync_state_line)

    def _label():
        # text only: a save in flight keeps its button disabled and loading
        set_label(save, _save_text())
        set_label(nxt, t("analysis_tile.nav.next"))
        set_label(discard, t("analysis_tile.verdict.discard"))
        set_label(stay, t("analysis_tile.verdict.stay"))
        prompt_title.children = [t("analysis_tile.verdict.unsaved_title")]
        prompt_hint.children = [t("analysis_tile.verdict.unsaved_hint")]

    labels.add(_label)
    if capture is not None:
        # The selection, and the table: a ticked main of another group brings
        # that whole group, which a new table may have changed.
        analysis.selection.subscribe(lambda *_: set_label(save, _save_text()))
        app_state.alerts_gdf.subscribe(lambda *_: set_label(save, _save_text()))

    root = v.Html(tag="div", class_="defo-analysis-footer", style_=FOOTER_STYLE, children=[
        export_root,
        buttons,
        prompt,
        state_line,
        # a dialog draws nothing in place
        confirm.dialog,
    ])
    setattr(root, _SLOTS, {"save": save, "next": nxt, "state_line": state_line,
                           "prompt": prompt, "discard": discard, "stay": stay})
    return SimpleNamespace(root=root, save=save, next=nxt, state_line=state_line, prompt=prompt, guard=guard,
                           confirm=confirm)
