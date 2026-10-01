"""Overview of the active alert set (spec §9.2, §9.3), a right-panel view.

Architecture layer: view. It renders the set's status summary and exports in
the right panel, and draws on the **single shared** ``SepalMap`` passed in
from the shell (it never creates a map of its own):

- **The set's tiles.** While the Overview view is open, the active set's
  ``alerts.pmtiles`` is drawn under the ``MapLayers`` key ``"overview:set"``
  and hidden again when the view closes. The build runs on a worker thread
  (tippecanoe, the tile server) and rebuilds tiles older than the set's
  ``alert_db.parquet`` from that store on disk (``set_store.ensure_pmtiles``),
  never from the alerts on hand. When tippecanoe is missing, or the tiles
  cannot be built or served, the alerts fall back to a GeoJSON layer of
  centroid points under the same key, with one warning toast (spec §9.2,
  §12). While the project is being written (an Integrate re-running the set,
  a review Save), stale tiles are not rebuilt: the centroids stand in without
  a toast, and the build is asked for again once the writer is done (the
  effect depends on ``project_writers``). A build that finds the view already
  closed does nothing. Published alerts that are still another set's (a new
  set is recorded before its alerts are published) count as not loaded yet,
  so nothing of the new set is drawn, counted or exported from them
  (``set_store.alerts_belong_to``).
- **Click to review.** While the view is open, a click on the map hit-tests the
  loaded alerts (``set_store.hit_alert``) and opens a popup with the alert's
  summary and **Review**, which selects that alert and opens Analysis. This
  replaces the status marker clusters. The popup is added with a key
  (``"overview:popup"``): pysepal's key lookups read ``.key`` on every layer,
  and a Popup added without one breaks them. It is removed when the view
  closes or the alerts change.

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
from ipyleaflet import GeoJSON, Popup
from pysepal import sepalwidgets as sw
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.message import cm
from deforestation_alerts.project import paths
from deforestation_alerts.scripts.alert_export import export_alerts_gdf
from deforestation_alerts.scripts.overview_helper import calculate_alert_classes
from gui.i18n import t
from gui.scripts import set_store
from gui.scripts.notify_bridge import current_notifier, tracked_job
from gui.scripts.pmtiles_map import STATUS_COLORS, add_set_pmtiles
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import is_writing, writing
from gui.store.workflow_steps import STEPS, active_set

#: MapLayers key of the active set's tiles (or their centroid fallback)
SET_LAYER_KEY = "overview:set"
#: map key of the click popup
POPUP_KEY = "overview:popup"
#: ``app_state.panel_view`` while the Overview view is open
OVERVIEW_VIEW = "overview"

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")


def _bind_export(gen_btn, dl_btn, fmt, gdf, workspace, folder, sources, start, end, project):
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

    Threading + feedback: writing a GPKG/GeoJSON and uploading it is blocking IO,
    so the work runs on a daemon thread via ``spawn_in_context`` (which carries
    the kernel context ``current_notifier`` resolves the bus from), under
    ``tracked_job``. A failure is a FAILED task + one error toast, not a silent
    log line.

    The export lands in the user's SEPAL workspace, so the button gets the
    HOME-RELATIVE remote path: ``set_url`` -> ``su.create_download_link`` builds
    ``https://sepal.io/api/sandbox/jupyter/files/<path relative to Path.home()>``,
    and in the container ``Path.home()`` is the *container's* home, so handing it
    an absolute path yields a dead link (inventory a18)."""

    def _on_click(widget, event, data):
        gen_btn.loading = True
        gen_btn.disabled = True

        def _worker():
            # One opaque phase (write + upload), so no ``total_steps``. On a
            # failure tracked_job fails the task and publishes the one error
            # toast (the raw exception text), then re-raises into the except.
            notifier = current_notifier()
            try:
                with tracked_job(
                    notifier, str(cm.overview_tile.export_task_title)
                ) as task:
                    task.step(
                        str(cm.overview_tile.export_step).format(fmt=fmt.upper())
                    )
                    with writing(project):
                        path = export_alerts_gdf(
                            gdf, workspace, folder, fmt, sources, start, end
                        )
                    url_path = workspace.home_relative(path)
            except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
                pass
            else:
                dl_btn.set_url(path=url_path)
                dl_btn.show()
                notifier.success(
                    str(cm.overview_tile.export_done).format(fmt=fmt.upper())
                )
            finally:
                gen_btn.loading = False
                gen_btn.disabled = False

        spawn_in_context(_worker)

    return _on_click


def build_export_card(gdf, workspace, folder, sources, start, end, project):
    """Build the export card: one generate button per format + its hidden
    DownloadBtn, revealed when that format has been written. ``project`` is
    the name of the project ``folder`` belongs to (see ``_bind_export``).

    A module function (not inline in the component) so the buttons a user
    actually clicks — including their enabled/hidden state — are directly
    testable; the component only decides *whether* to show the card.
    """
    gen_buttons = []
    dl_buttons = []
    for fmt in ("csv", "gpkg", "geojson"):
        label = str(getattr(cm.overview_tile, f"export_{fmt}"))
        gen_btn = sw.Btn(
            msg=label, small=True, outlined=True, color="primary", class_="pa-1 ma-1"
        )
        dl_btn = sw.DownloadBtn(
            text=f"{cm.overview_tile.export_download} {label}",
            small=True,
            class_="pa-1 ma-1",
        ).hide()
        gen_btn.on_event(
            "click",
            _bind_export(
                gen_btn, dl_btn, fmt, gdf, workspace, folder, sources, start, end, project
            ),
        )
        gen_buttons.append(gen_btn)
        dl_buttons.append(dl_btn)

    return v.Card(
        class_="pa-3 ma-3",
        hover=True,
        children=[
            v.CardTitle(class_="pa-1", children=[cm.overview_tile.export_label]),
            v.Row(class_="d-flex justify-center", children=gen_buttons),
            v.Row(class_="d-flex justify-center", children=dl_buttons),
        ],
    )


# Summary-table row labels (matches old tile order)
_ALERT_LABELS = [
    cm.overview_tile.total,
    cm.overview_tile.reviewed,
    cm.overview_tile.confirmed,
    cm.overview_tile.false_positives,
    cm.overview_tile.revision,
]


def _make_summary_rows(counts: list[int], labels: list[str]) -> list:
    """Return ipyvuetify <tr> widgets for the summary table.

    Local copy of the pure UI part of overview_helper.create_table_rows, kept
    here so the service module's ipyvuetify dependency does not leak into this
    component through a function call.  The service version is left unchanged.
    """
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


def _centroid_style(feature):
    color = STATUS_COLORS.get(feature["properties"].get("status_code"), STATUS_COLORS[0])
    return {"color": color, "fillColor": color}


#: The centroid layer's own style. It must be non-empty and carry no colour:
#: ipyleaflet's GeoJSON view draws each feature with
#: ``{...feature.properties.style, ...layer.style}``, so the layer's style wins,
#: and ``SepalMap.add_layer`` gives a layer with an empty style pysepal's
#: default (hollow, weight 2, the theme's primary colour), which would paint
#: every point alike. The per-status colours come from ``_centroid_style``.
_CENTROID_LAYER_STYLE = {"stroke": True, "weight": 1, "opacity": 1, "fill": True, "fillOpacity": 0.9}


def _add_centroid_layer(map_, alerts, name: str) -> None:
    """Draw ``alerts`` as centroid points coloured by status under
    ``SET_LAYER_KEY``: the fallback when the set's tiles cannot be shown
    (spec §9.2, §12)."""
    layer = GeoJSON(
        data=set_store.centroid_geojson(alerts),
        point_style={"radius": 4},
        style=_CENTROID_LAYER_STYLE,
        style_callback=_centroid_style,
        name=name,
    )
    map_.add_layer(layer, key=SET_LAYER_KEY)


def _set_alerts(project, gdf):
    """``gdf`` when it holds the active set's alerts, else None.

    ``alerts_gdf`` and the active set are separate reactives: Integrate records
    a new set before it publishes that set's alerts, and the set selector
    (spec §9.3) loads them in a worker after the selection. Until they arrive,
    the alerts on hand are the previous set's, and the tile treats them as not
    loaded yet: it never tiles, draws, counts, exports or reviews them as the
    new set's. Without an active set, ``gdf`` is kept as it is.
    """
    s = active_set(project)
    return gdf if s is None or set_store.alerts_belong_to(gdf, s) else None


def _error_text(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


@solara.component
def OverviewTile(app_state, map_, workspace, layers):
    """Overview of the active alert set (spec §9.3): the status summary, exports,
    the set's PMTiles on the shared map while this view is open, and a click on
    the map opens the nearest alert in Analysis.

    Parameters
    ----------
    app_state : AppState
        The session state: the project (its active set), ``alerts_gdf`` (the
        active set's alerts, loaded by the shell), ``panel_view`` and the
        navigation reactives.
    map_ : SepalMap
        The single shared map.
    workspace : Workspace
        The session user-file adapter the exports are written through, into the
        set's own folder.
    layers : MapLayers
        The session's map-layer registry; the set's layer is its
        ``"overview:set"`` key.
    """
    notifications = use_notifications()
    # Summary table body, mutated in place by _sync_table: a concrete widget,
    # so the table below is raw ipyvuetify, not a reacton element.
    tbody_ref = solara.use_memo(
        lambda: v.Html(tag="tbody", children=_make_summary_rows([0, 0, 0, 0, 0], _ALERT_LABELS)), []
    )
    popup_ref = solara.use_ref(None)
    p = app_state.project.value
    # None while the published alerts are still another set's (_set_alerts):
    # the publish that follows re-runs everything keyed on it.
    gdf = _set_alerts(p, app_state.alerts_gdf.value)
    active = app_state.panel_view.value == OVERVIEW_VIEW
    s = active_set(p)
    has_alerts = gdf is not None and len(gdf) > 0
    # Subscribes to project_writers: a build skipped while the project was
    # being written (``deferred``) is asked for again once the writer is done,
    # through ``retries``. Only a deferred build re-keys the layer effect, so
    # a Save's own mark does not redraw tiles that are current.
    busy = is_writing(p.name) if p is not None else False
    deferred = solara.use_ref(False)
    retries, set_retries = solara.use_state(0)

    def _retry_when_free():
        if not busy and deferred.current:
            deferred.current = False
            set_retries(retries + 1)

    solara.use_effect(_retry_when_free, [busy])

    def _sync_table():
        counts = (calculate_alert_classes(gdf, "Confirmed", "False Positive", "Need revision")
                  if has_alerts else [0, 0, 0, 0, 0])
        tbody_ref.children = _make_summary_rows(counts, _ALERT_LABELS)

    solara.use_effect(_sync_table, [gdf])

    def _sync_layer():
        if not active or s is None or not has_alerts:
            layers.hide(SET_LAYER_KEY)
            return
        project, alert_set, alerts = p, s, gdf
        name = t("steps.overview.layer_name")

        def _still_open():
            # Ruling #57: a build that starts, or finishes tippecanoe, after the
            # view closed does nothing more (the hide has already bumped the
            # key, so MapLayers would drop its layer anyway). Only the view is
            # checked, not ``layers.is_on``: the published ``on`` can lag a
            # moment behind a show while another key's worker re-publishes,
            # and a false "off" here would leave the set undrawn.
            return app_state.panel_view.value == OVERVIEW_VIEW

        def _defer():
            # Called by ensure_pmtiles right before it raises StoreBusy, while
            # its own writer mark is still held: releasing that mark is what
            # re-runs _retry_when_free, and it happens before the exception
            # reaches the except below, which would note the retry too late.
            deferred.current = True

        def build():
            if not _still_open():
                return
            deferred.current = False
            try:
                pm = set_store.ensure_pmtiles(project, alert_set, workspace, on_wait=_defer)
                warning = None if pm is not None else t("steps.integrate.no_tippecanoe")
            except set_store.StoreBusy as exc:
                # Stale tiles while the project is being written (an Integrate
                # swapping this set's store in, a review Save), or a store
                # rewritten under the build: the centroids stand in, silently,
                # until the writer is done.
                logger.info("tiles of set %s not rebuilt now: %s", alert_set.id, exc)
                deferred.current = True  # already noted by _defer when a mark was involved
                pm, warning = None, None
            except Exception as exc:  # noqa: BLE001 - the centroids stand in for the tiles
                logger.exception("could not build the tiles of set %s", alert_set.id)
                pm, warning = None, t("steps.overview.tiles_failed", error=_error_text(exc))
            if not _still_open():
                return
            if pm is not None:
                try:
                    add_set_pmtiles(map_, pm, key=SET_LAYER_KEY, name=name)
                    return
                except Exception as exc:  # noqa: BLE001 - the centroids stand in for the tiles
                    logger.exception("could not show the tiles of set %s", alert_set.id)
                    warning = t("steps.overview.tiles_failed", error=_error_text(exc))
            if warning is not None:
                notifications.warning(warning)
            _add_centroid_layer(map_, alerts, name)

        layers.show(SET_LAYER_KEY, build, content_id=f"{alert_set.id}:{len(alerts)}", notifier=notifications)

    solara.use_effect(_sync_layer, [active, s.id if s else None, gdf, retries])

    def _close_popup():
        if popup_ref.current is not None:
            map_.remove_layer(popup_ref.current, none_ok=True)
            popup_ref.current = None

    def _on_map(**kwargs):
        if kwargs.get("type") != "click" or app_state.panel_view.value != OVERVIEW_VIEW:
            return
        lat, lon = kwargs["coordinates"]
        current = _set_alerts(app_state.project.value, app_state.alerts_gdf.value)
        i = set_store.hit_alert(current, lon, lat)
        _close_popup()
        if i is None:
            return
        row = current.iloc[i]
        go = v.Btn(small=True, color="primary", children=[t("steps.overview.go")])

        def _go(*_):
            app_state.actual_alert_id.value = i
            app_state.current_page_view.value = "analysis_tile"

        go.on_event("click", _go)
        summary = t("steps.overview.popup", id=int(row.get("alert_id", i)), status=row.get("status", ""),
                    area=f"{float(row.get('area_ha', 0)):.2f}")
        body = v.Html(tag="div", children=[v.Html(tag="div", children=[summary]), go])
        popup = Popup(location=(lat, lon), child=body, close_button=True, auto_close=True)
        map_.add_layer(popup, key=POPUP_KEY)
        popup_ref.current = popup

    def _wire_clicks():
        if not active:
            return None
        map_.on_interaction(_on_map)

        def _unwire():
            map_.on_interaction(_on_map, remove=True)
            _close_popup()  # also after the user closed it: a closed Popup stays in map_.layers

        return _unwire

    # Keyed on the alerts too: a popup's Review index belongs to the alerts it
    # was opened on.
    solara.use_effect(_wire_clicks, [active, gdf])

    # NOTE: raw ipyvuetify `v.SimpleTable` (not reacton `rv.*`) on purpose: the
    # effect mutates `tbody_ref.children` in place, which requires a concrete
    # widget instance.
    info_table = solara.use_memo(lambda: v.SimpleTable(dense=False, children=[tbody_ref]), [])
    info_card = rv.Card(class_="pa-3 ma-3", hover=True,
                        children=[rv.CardTitle(class_="pa-1 ma-1", children=[cm.overview_tile.title]), info_table])

    # The export card is built only when its inputs change, so a re-render (a
    # project save, a view switch) keeps the download buttons already revealed.
    period = p.periods.get(s.period_id) if s is not None else None
    folder = str(set_store.set_folder(p, s)) if s is not None else None
    sources = tuple(s.sources) if s is not None else ()
    start = period.start.isoformat() if period is not None else ""
    end = period.end.isoformat() if period is not None else ""
    export_card = solara.use_memo(
        lambda: build_export_card(gdf, workspace, folder, list(sources), start, end, p.name)
        if s is not None and has_alerts else None,
        [gdf, folder, sources, start, end],
    )

    def _back_to_workflow():
        # Return the right panel to the Workflow view at the step the user was
        # on; the navigation bridge also un-highlights the Overview rail item.
        app_state.current_page_view.value = STEPS[app_state.active_step.value].page_view

    def _review_alerts():
        app_state.current_page_view.value = "analysis_tile"

    with solara.Column(style="padding: 0;"):
        with solara.Row(style="gap: 8px; align-items: center; padding: 0 12px;"):
            solara.Button(cm.overview_tile.back_to_workflow, icon_name="mdi-arrow-left", text=True, small=True,
                          on_click=_back_to_workflow)
            solara.v.Spacer()
            solara.Button(cm.overview_tile.review_button, color="primary", small=True, disabled=not has_alerts,
                          on_click=_review_alerts)
        if s is not None:
            solara.Text(t("steps.overview.set_label", period=s.period_id, sources=" + ".join(s.sources),
                          n=s.n_alerts, reviewed=s.n_reviewed), style="padding: 0 16px;")
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
