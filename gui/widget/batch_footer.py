"""The footer under the Download & harmonize table (spec "Footer").

Idle, it is a primary split button on the right: the main part runs every
pending row (``Run {n} pending``), and its caret shows the other batch actions
as buttons like it, stacked under it at its width; with no menu item there is
no caret. While the scope has
a job, the left side says how many of its rows run and wait
(``{running} running · {queued} queued``), with a thin bar and
``{done} of {total} done`` while a batch is counted, and the main part turns
into an outlined ``Stop``: it stops after the row in progress. Once
stopped, until the job ends, it is a disabled outlined ``Stopping…``, and the
strip drops its queued half (those rows will not run).

The spatial_risk app's list footer, without its queue: every click of the step
starts its own job, so the counts come from the claimed rows and the stage
each running row reports (``footer_counts``).
"""

from typing import Callable, Optional

import reacton.ipyvuetify as rv
import solara

from gui.i18n import format_number, t

_WRAP = "defo-batch-split"
# The two halves of the split button touch: square inner corners, a thin gap;
# the caret's items stack under it at the width of the widest.
_CSS = (
    f".{_WRAP} {{ display: inline-flex; flex-direction: column; gap: 4px; }}"
    f" .{_WRAP} > .defo-split-row {{ display: flex; }}"
    f" .{_WRAP} .v-btn.defo-split-main {{ flex: 1 1 auto; }}"
    f" .{_WRAP} .v-btn.defo-split-main {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}"
    f" .{_WRAP} .v-btn.defo-split-caret {{ border-top-left-radius: 0; border-bottom-left-radius: 0;"
    " min-width: 28px !important; padding: 0 4px !important; margin-left: 1px; }"
)
_TEXT = "font-size: 0.85rem; opacity: 0.75;"
# A disabled button or list item takes no pointer events: its tooltip sits on a span around it.
_TIP = "display: inline-flex;"


def footer_counts(mine_rows: set, stages: dict, batch: Optional[tuple]) -> dict:
    """The footer's counts; pure. ``mine_rows``: the progress keys of the
    rows this scope's jobs claimed; ``stages``: the running rows' stages by
    progress key (already this scope's); ``batch``: ``(total, done)`` of the
    scope's jobs, None when none was counted. A claimed row with a stage is
    running, one without is queued."""
    running = len(set(mine_rows) & set(stages))
    total, done = batch if batch is not None else (None, None)
    return {"running": running, "queued": len(mine_rows) - running, "done": done, "total": total}


def _caret(open_: bool, on_click: Callable[[], None]):
    label = t("steps.prepare.footer_more")
    solara.Button(icon_name="mdi-menu-up" if open_ else "mdi-menu-down", color="primary", small=True,
                  classes=["defo-split-caret"], on_click=on_click,
                  attributes={"title": label, "aria-label": label, "aria-expanded": str(open_).lower()})


def _item(item: dict, on_done: Callable[[], None]):
    """A caret item: a button like Run (the caret closes once it is clicked)."""
    tip = item.get("title", "")

    def click():
        on_done()
        item["on_click"]()

    with rv.Html(tag="span", attributes={"title": tip} if tip else {}, style_=_TIP):
        solara.Button(item["label"], icon_name=item.get("icon"), color="primary", small=True,
                      disabled=item.get("disabled", False), on_click=click, style="flex: 1 1 auto;")


@solara.component
def BatchFooter(pending: int, run_label: str, on_run: Callable[[], None], on_stop: Callable[[], None],
                menu: list, counts: dict, disabled: bool = False, stopping: bool = False):
    """The batch footer (render it under the table).

    ``pending``: the rows Run would start (none: the main part is
    disabled); ``run_label``: its counted label; ``on_run`` / ``on_stop``: the
    main part's clicks, idle and busy; ``menu``: the caret's items,
    ``{"label", "on_click", "icon"?, "disabled"?, "title"?}`` (``title``: the tooltip,
    kept while disabled); ``counts``: ``footer_counts``; ``disabled``: Run
    cannot start anything (a review copy); ``stopping``: Stop was clicked
    and the job has not ended yet.
    """
    busy = counts["running"] + counts["queued"] > 0
    open_, set_open = solara.use_state(False)
    solara.Style(_CSS)
    with solara.Row(style="align-items: center; justify-content: space-between; gap: 12px;"):
        if busy:
            with solara.Column(style="flex: 1 1 auto; min-width: 0; gap: 2px;"):
                strip = (t("steps.prepare.footer_running", running=format_number(counts["running"])) if stopping
                         else t("steps.prepare.footer_strip", running=format_number(counts["running"]),
                                queued=format_number(counts["queued"])))
                solara.Text(strip, style=_TEXT)
                if counts["total"]:
                    rv.ProgressLinear(value=round(100 * counts["done"] / counts["total"]), height=3,
                                      color="primary")
                    solara.Text(t("steps.prepare.footer_done", done=format_number(counts["done"]),
                                  total=format_number(counts["total"])), style=_TEXT)
        with rv.Html(tag="div", class_=_WRAP, style_="flex: 0 0 auto; margin-left: auto;"):
            if busy:
                tip = t("steps.prepare.footer_stop_tip")
                with rv.Html(tag="span", attributes={"title": tip, "aria-label": tip}, style_=_TIP):
                    if stopping:
                        solara.Button(t("steps.prepare.footer_stopping"), icon_name="mdi-stop", color="primary",
                                      small=True, outlined=True, disabled=True)
                    else:
                        solara.Button(t("steps.prepare.footer_stop"), icon_name="mdi-stop", color="primary",
                                      small=True, outlined=True, on_click=on_stop)
            else:
                with rv.Html(tag="div", class_="defo-split-row"):
                    solara.Button(run_label, icon_name="mdi-play", color="primary", small=True,
                                  disabled=disabled or not pending, on_click=on_run,
                                  classes=["defo-split-main"] if menu else [])
                    if menu:
                        _caret(open_, lambda: set_open(not open_))
                if menu and open_:
                    for item in menu:
                        _item(item, lambda: set_open(False))
