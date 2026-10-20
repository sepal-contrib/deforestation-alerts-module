"""The Analysis panel's sticky footer (spec D6, layout A): the Export
section, then Save & next / Save, then the save-state line, pinned to the
bottom of the Workflow panel so the review rhythm (answer, Save & next) never
needs a scroll, however many questions the project asks.

Architecture layer: view (raw-widget builder, no ``@solara.component``),
built once per session with the rest of the panel; the reactive subscriptions
below are registered once and their unsubscribers deliberately discarded.

**Save & next** is the panel's one primary button: it saves and advances only
if the save wrote (spec §4). Both buttons drive the export section's
``run_save`` -- there is one save runner in the panel -- and are registered
with it (``run_save.guard``) so a save started from one cannot be raced by
the other over the same store and row.

The background is opaque: the panel scrolls under the footer. The inline
colour is the light theme's (``--v-background-base`` is not themed, so its
fallback is what renders); the dark theme's is restated in the shell's
stylesheet (``solara_app._AppShell``: ``.theme--dark .defo-analysis-footer``).
"""

from datetime import datetime
from types import SimpleNamespace

import ipyvuetify as v

from gui.i18n import t
from gui.tile.analysis.labels import Labels
from gui.widget.buttons import btn, set_label

# ``margin-bottom: -32px`` cancels the Workflow panel's own bottom space (its
# 16px padding and the 16px under each section): a sticky element stays inside
# its parent, so without it the footer rose 32px off the panel's bottom edge
# once the content was scrolled to its end.
FOOTER_STYLE = ("position: sticky; bottom: 0; z-index: 3; padding: 8px 8px 10px; "
                "margin-top: 8px; margin-bottom: -32px; "
                "background-color: var(--v-background-base, #ffffff); "
                "border-top: 1px solid rgba(128, 128, 128, 0.25); box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.08);")
_SLOTS = "_defo_footer_slots"


def footer_widgets(root):
    """``(save_next, save, state_line)`` of a footer ``build_footer`` built."""
    s = getattr(root, _SLOTS)
    return s["save_next"], s["save"], s["state_line"]


def build_footer(run_save, export_root, app_state, analysis, *, labels=None):
    """Build and return ``SimpleNamespace(root, save_next, save, state_line)``.

    ``run_save`` is the export section's runner (``export.make_save_runner``),
    ``export_root`` the Export section's card, placed first in the footer.
    ``labels`` is the screen's ``labels.Labels`` (a fresh one when none is
    given): the buttons' words and the state line follow a language switch.
    """
    labels = labels if labels is not None else Labels()
    # The end of an alert's review, drawn as the workflow's *Start analysis*:
    # the screen's one default-size (36 px) filled button. *Save* is its
    # outlined twin.
    save_next = btn("primary", t("analysis_tile.verdict.save_next"), icon_right="mdi-arrow-right",
                    small=False, class_="flex-grow-1")
    save = btn("secondary", t("analysis_tile.export_labels.save_btn"), icon="mdi-content-save-outline",
               small=False)
    state_line = v.Html(tag="div", class_="dat-caption mt-2", children=[])

    save.on_event("click", lambda *_: run_save(save))
    save_next.on_event("click", lambda *_: run_save(save_next, then=app_state.go_next))
    # Both buttons are the same save: the runner holds them together for its
    # duration. ``getattr`` because a test may inject a plain callable.
    guard = getattr(run_save, "guard", None)
    if guard is not None:
        guard(save_next, save)

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
        # text only: a save in flight keeps its buttons disabled and loading
        set_label(save_next, t("analysis_tile.verdict.save_next"))
        set_label(save, t("analysis_tile.export_labels.save_btn"))

    labels.add(_label)

    root = v.Html(tag="div", class_="defo-analysis-footer", style_=FOOTER_STYLE, children=[
        export_root,
        v.Html(tag="div", class_="d-flex mt-2 px-1", style_="gap: 8px;", children=[save_next, save]),
        v.Html(tag="div", class_="px-1", children=[state_line]),
    ])
    setattr(root, _SLOTS, {"save_next": save_next, "save": save, "state_line": state_line})
    return SimpleNamespace(root=root, save_next=save_next, save=save, state_line=state_line)
