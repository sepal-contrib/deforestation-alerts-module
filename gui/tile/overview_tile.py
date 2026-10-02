"""Overview of the active alert set (spec §9.2, §9.3), a right-panel view.

The set shown is picked with the set selector (``gui.widget.set_selector``,
shared with Analysis, spec §9.3): a pick changes only the project's
``active_set``, and the shell's loader then loads that set's alerts.

Architecture layer: view. It reads; it draws nothing on the map. The alert
layers, the earlier sets and the click-to-review popup moved to the shell's
``_AlertLayers`` leaf, switched on from Integrate & vectorize, because a layer
switched on there stays on while the user moves between steps and views, and
a view that owns a layer must hide it when it closes.

Feedback is hybrid. The component reports map-layer problems through
``use_notifications()``. The export card (``build_export_card``) is a
module-level RAW ipyvuetify builder whose worker runs on a daemon thread
(``spawn_in_context``), where no Solara hook can run, so, like
``analysis/export``, that worker resolves a notifier from its kernel context
(``notify_bridge.current_notifier``) and runs its body under
``notify_bridge.tracked_job``. ``tracked_job`` opens the task, turns the
worker thread's app log lines into its milestones, and on a failure marks it
FAILED before publishing the one error toast.

``calculate_alert_classes`` comes from the service module ``overview_helper``
(it is pure); the summary rows are built locally by ``_make_summary_rows`` so
that module's ipyvuetify helpers stay out of this component.
"""

import logging

import ipyvuetify as v
import reacton.ipyvuetify as rv
import solara
from pysepal import sepalwidgets as sw
from pysepal.i18n import current_locale
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.project import paths
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_export import export_alerts_gdf
from deforestation_alerts.scripts.overview_helper import calculate_alert_classes
from gui.i18n import t
from gui.scripts import loss_drivers as drivers
from gui.scripts import set_store
from gui.scripts.notify_bridge import current_notifier, tracked_job
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writing
from gui.store.workflow_steps import STEPS, active_set
from gui.widget.set_selector import SetSelector

#: ``app_state.panel_view`` while the Overview view is open
OVERVIEW_VIEW = "overview"

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")


def _bind_export(gen_btn, dl_btn, fmt, gdf, folder, sources, start, end, project):
    """Return the click handler that generates the ``fmt`` export and reveals its
    download button. ``project`` is the name of the project the set folder
    belongs to, captured when the card was built: the export marks it as
    being written for its duration (spec §12), so a delete is refused instead
    of the export re-creating the deleted folder.

    TWO buttons, not one: ``sw.DownloadBtn`` starts life *disabled* (its default
    ``path="#"`` makes ``set_url`` disable it), so a DownloadBtn wired to a click
    handler can never be clicked and the export would never run. The generate
    action therefore lives on a plain ``sw.Btn``; the DownloadBtn is built hidden
    and is revealed — enabled, with its URL — only once a file actually exists.
    Same pattern as ``analysis/export.py``'s save -> downloads toolbar.

    Threading + feedback: writing a GPKG/GeoJSON is blocking IO,
    so the work runs on a daemon thread via ``spawn_in_context`` (which carries
    the kernel context ``current_notifier`` resolves the bus from), under
    ``tracked_job``. A failure is a FAILED task + one error toast, not a silent
    log line.

    The export lands in the set folder, and the download link is relative to
    ``$HOME``: ``set_url`` -> ``su.create_download_link`` builds
    ``https://sepal.io/api/sandbox/jupyter/files/<path relative to $HOME>``
    (``local_files.home_relative``)."""

    def _on_click(widget, event, data):
        gen_btn.loading = True
        gen_btn.disabled = True

        def _worker():
            # One opaque phase (the write), so no ``total_steps``. On a
            # failure tracked_job fails the task and publishes the one error
            # toast (the raw exception text), then re-raises into the except.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier, t("overview_tile.export_task_title")
                ) as task:
                    task.step(t("overview_tile.export_step", fmt=fmt.upper()))
                    with writing(project):
                        # the loss drivers in the export's language (the
                        # store keeps their codes, see gui.scripts.loss_drivers)
                        path = export_alerts_gdf(
                            drivers.with_labels(gdf), folder, fmt, sources, start, end
                        )
                    url_path = local_files.home_relative(path)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                dl_btn.set_url(path=url_path)
                dl_btn.show()
                notifier.success(t("overview_tile.export_done", fmt=fmt.upper()))
            finally:
                gen_btn.loading = False
                gen_btn.disabled = False

        spawn_in_context(_worker)

    return _on_click


EXPORT_FORMATS = ("csv", "gpkg", "geojson")


def build_export_card(gdf, folder, sources, start, end, project):
    """Build the export card: one generate button per format + its hidden
    DownloadBtn, revealed when that format has been written. ``project`` is
    the name of the project ``folder`` belongs to (see ``_bind_export``).

    A module function (not inline in the component) so the buttons a user
    actually clicks — including their enabled/hidden state — are directly
    testable; the component only decides *whether* to show the card.
    """
    gen_buttons = []
    dl_buttons = []
    for fmt in EXPORT_FORMATS:
        gen_btn = sw.Btn(
            msg="", small=True, outlined=True, color="primary", class_="pa-1 ma-1"
        )
        dl_btn = sw.DownloadBtn(text="", small=True, class_="pa-1 ma-1").hide()
        gen_btn.on_event(
            "click",
            _bind_export(
                gen_btn, dl_btn, fmt, gdf, folder, sources, start, end, project
            ),
        )
        gen_buttons.append(gen_btn)
        dl_buttons.append(dl_btn)

    card = v.Card(
        class_="pa-3 ma-3",
        hover=True,
        children=[
            v.CardTitle(class_="pa-1", children=[""]),
            v.Row(class_="d-flex justify-center", children=gen_buttons),
            v.Row(class_="d-flex justify-center", children=dl_buttons),
        ],
    )
    return relabel_export_card(card)


def relabel_export_card(card):
    """Write the export card's labels in the current language, in place: the
    card is built once per set, so a language switch relabels it rather than
    rebuilding it (which would hide the download buttons already revealed)."""
    title, gen_row, dl_row = card.children
    title.children = [t("overview_tile.export_label")]
    for fmt, gen_btn, dl_btn in zip(EXPORT_FORMATS, gen_row.children, dl_row.children):
        label = t(f"overview_tile.export_{fmt}")
        gen_btn.msg = label
        dl_btn.children = [dl_btn.children[0], f"{t('overview_tile.export_download')} {label}"]
    return card


# Summary-table row label keys (matches old tile order); translated where the
# rows are built, never at import (that would freeze English).
_ALERT_LABEL_KEYS = (
    "overview_tile.total",
    "overview_tile.reviewed",
    "overview_tile.confirmed",
    "overview_tile.false_positives",
    "overview_tile.revision",
)


def _make_summary_rows(counts: list[int], labels: list[str]) -> list:
    """Return ipyvuetify <tr> widgets for the summary table: one row per
    label, with its count."""
    return [
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=[label]),
                v.Html(tag="td", children=[str(value)]),
            ],
        )
        for label, value in zip(labels, counts)
    ]


@solara.component
def OverviewTile(app_state, map_, layers):
    """Overview of the active alert set (spec §9.3): the status summary and the
    exports, over the set picked with the set selector.

    Parameters
    ----------
    app_state : AppState
        The session state: the project (its active set), ``alerts_gdf`` (the
        active set's alerts, loaded by the shell), ``panel_view`` and the
        navigation reactives.
    map_ : SepalMap
        The single shared map. Unused: this view draws nothing on it (the
        alert layers are the shell's ``_AlertLayers``), and it is passed only
        so the signature matches the other views the shell mounts.
    layers : MapLayers
        The session's map-layer registry. Unused, as ``map_``.
    """
    notifications = use_notifications()
    # Summary table body, mutated in place by _sync_table: a concrete widget,
    # so the table below is raw ipyvuetify, not a reacton element.
    tbody_ref = solara.use_memo(
        lambda: v.Html(tag="tbody", children=_make_summary_rows([0, 0, 0, 0, 0], [t(k) for k in _ALERT_LABEL_KEYS])), []
    )
    p = app_state.project.value
    # None while the published alerts are still another set's: the publish that
    # follows re-renders the tile on them.
    gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
    s = active_set(p)
    # Read in the body so a language switch re-renders the tile, and keyed
    # below so the raw rows are rebuilt in the new language.
    has_alerts = gdf is not None and len(gdf) > 0
    locale = current_locale()

    def _sync_table():
        counts = (calculate_alert_classes(gdf, "Confirmed", "False Positive", "Need revision")
                  if has_alerts else [0, 0, 0, 0, 0])
        tbody_ref.children = _make_summary_rows(counts, [t(k) for k in _ALERT_LABEL_KEYS])

    solara.use_effect(_sync_table, [gdf, locale])

    # NOTE: raw ipyvuetify `v.SimpleTable` (not reacton `rv.*`) on purpose: the
    # effect mutates `tbody_ref.children` in place, which requires a concrete
    # widget instance.
    info_table = solara.use_memo(lambda: v.SimpleTable(dense=False, children=[tbody_ref]), [])
    info_card = rv.Card(class_="pa-3 ma-3", hover=True,
                        children=[rv.CardTitle(class_="pa-1 ma-1", children=[t("overview_tile.title")]), info_table])

    # The export card is built only when its inputs change, so a re-render (a
    # project save, a view switch) keeps the download buttons already revealed.
    period = p.periods.get(s.period_id) if s is not None else None
    folder = str(set_store.set_folder(p, s)) if s is not None else None
    sources = tuple(s.sources) if s is not None else ()
    start = period.start.isoformat() if period is not None else ""
    end = period.end.isoformat() if period is not None else ""
    export_card = solara.use_memo(
        lambda: build_export_card(gdf, folder, list(sources), start, end, p.name)
        if s is not None and has_alerts else None,
        [gdf, folder, sources, start, end],
    )

    def _relabel_export():
        if export_card is not None:
            relabel_export_card(export_card)

    # Its labels follow a language switch on the same card (``locale`` read above).
    solara.use_effect(_relabel_export, [export_card, locale])

    def _back_to_workflow():
        # Return the right panel to the Workflow view at the step the user was
        # on; the navigation bridge also un-highlights the Overview rail item.
        app_state.current_page_view.value = STEPS[app_state.active_step.value].page_view

    def _review_alerts():
        app_state.current_page_view.value = "analysis_tile"

    with solara.Column(style="padding: 0;"):
        with solara.Row(style="gap: 8px; align-items: center; padding: 0 12px;"):
            solara.Button(t("overview_tile.back_to_workflow"), icon_name="mdi-arrow-left", text=True, small=True,
                          on_click=_back_to_workflow)
            solara.v.Spacer()
            solara.Button(t("overview_tile.review_button"), color="primary", small=True, disabled=not has_alerts,
                          on_click=_review_alerts)
        if s is not None:
            with solara.Div(style="padding: 0 16px;"):
                SetSelector(app_state=app_state)
            status = set_status(p, paths.project_dir(p.name), s)
            if status is Status.STALE:
                solara.Text(t("steps.overview.stale"), classes=["warning--text"], style="padding: 0 16px;")
            elif status is Status.MISSING:
                solara.Text(t("steps.overview.missing"), classes=["warning--text"], style="padding: 0 16px;")
        solara.display(info_card)
        if export_card is not None:
            # A child of a reacton element, like the raw info table above, so
            # the card is part of the rendered tree itself.
            rv.Html(tag="div", children=[export_card])
