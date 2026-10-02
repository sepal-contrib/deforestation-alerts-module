"""Overview of the active alert set (spec §9.2, §9.3), a right-panel view.

The set shown is picked with the set selector (``gui.widget.set_selector``,
shared with Analysis, spec §9.3): a pick changes only the project's
``active_set``, and the shell's loader then loads that set's alerts.

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
- **Earlier sets.** With 'Show earlier sets' on, the sets of earlier periods
  (the footprints the active set subtracts, ``earlier_of``) are drawn
  lightened under ``overview:set:<set id>``, each with its legend, and a click
  on one of their alerts offers Go, which makes that set active and opens the
  alert in Analysis. Each one's build (a ``MapLayers`` worker) reads that
  set's store for the hit test, keeps it keyed by (project, set id, set
  fingerprint), and draws its tiles, or its lightened centroids when the
  tiles cannot be built (silently: the active set already warned) or the
  project is being written. A set whose store is gone is refused with an
  error toast.
- **Click to review.** While the view is open, a click on the map hit-tests the
  loaded alerts (``set_store.hit_alert``): the active set's first, then the
  earlier sets shown, newest first. It opens a popup with the alert's summary
  (and, for an earlier set, its name) and **Review** (``set_store.go_to_alert``),
  which selects that alert, switching set first if needed, and opens Analysis.
  This replaces the status marker clusters. The popup is added with a key
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
from pysepal.i18n import current_locale
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import Status, earlier_sets, set_status
from deforestation_alerts.errors import UserFileMissing
from deforestation_alerts.project import paths
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_export import export_alerts_gdf
from deforestation_alerts.scripts.overview_helper import calculate_alert_classes
from gui.i18n import format_number, t
from gui.scripts import loss_drivers as drivers
from gui.scripts import set_store
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier, tracked_job
from gui.scripts.legend_specs import status_colors
from gui.scripts.pmtiles_map import add_set_pmtiles
from gui.scripts.source_layers import earlier_set_legend, earlier_set_name, set_legend
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import is_writing, writing
from gui.store.workflow_steps import STEPS, active_set
from gui.widget.set_selector import SetSelector

#: MapLayers key of the active set's tiles (or their centroid fallback)
SET_LAYER_KEY = "overview:set"
#: map key of the click popup
POPUP_KEY = "overview:popup"
#: ``app_state.panel_view`` while the Overview view is open
OVERVIEW_VIEW = "overview"
#: MapLayers key prefix of the earlier sets' layers (spec §4.3)
EARLIER_PREFIX = "overview:set:"

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


def earlier_of(project, alert_set) -> list:
    """The sets whose footprints ``alert_set`` subtracts (periods that start
    before its own), newest first: what "Show earlier sets" draws."""
    period = project.periods.get(alert_set.period_id) if alert_set is not None else None
    if period is None:
        return []

    def _key(e):
        return (project.periods[e.period_id].start, e.created_at)

    return sorted(earlier_sets(project, period), key=_key, reverse=True)


def _centroid_style_for(faded: bool):
    colors = status_colors(faded)

    def _style(feature):
        color = colors.get(feature["properties"].get("status_code"), colors[0])
        return {"color": color, "fillColor": color}

    return _style



#: The centroid layer's own style. It must be non-empty and carry no colour:
#: ipyleaflet's GeoJSON view draws each feature with
#: ``{...feature.properties.style, ...layer.style}``, so the layer's style wins,
#: and ``SepalMap.add_layer`` gives a layer with an empty style pysepal's
#: default (hollow, weight 2, the theme's primary colour), which would paint
#: every point alike. The per-status colours come from ``_centroid_style_for``.
_CENTROID_LAYER_STYLE = {"stroke": True, "weight": 1, "opacity": 1, "fill": True, "fillOpacity": 0.9}


def _add_centroid_layer(map_, alerts, name: str, key: str = SET_LAYER_KEY, faded: bool = False) -> None:
    """Draw ``alerts`` as centroid points coloured by status under ``key``
    (lightened for an earlier set): the fallback when the set's tiles cannot
    be shown (spec §9.2, §12)."""
    layer = GeoJSON(
        data=set_store.centroid_geojson(alerts),
        point_style={"radius": 4},
        style=_CENTROID_LAYER_STYLE,
        style_callback=_centroid_style_for(faded),
        name=name,
    )
    map_.add_layer(layer, key=key)


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


@solara.component
def OverviewTile(app_state, map_, layers):
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
    layers : MapLayers
        The session's map-layer registry; the set's layer is its
        ``"overview:set"`` key.
    """
    notifications = use_notifications()
    # Summary table body, mutated in place by _sync_table: a concrete widget,
    # so the table below is raw ipyvuetify, not a reacton element.
    tbody_ref = solara.use_memo(
        lambda: v.Html(tag="tbody", children=_make_summary_rows([0, 0, 0, 0, 0], [t(k) for k in _ALERT_LABEL_KEYS])), []
    )
    popup_ref = solara.use_ref(None)
    # (project, set id) pairs already told that tippecanoe is missing: a review
    # Save re-publishes the alerts and redraws the centroids, without the warning.
    warned = solara.use_ref(set())
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

    # Read in the body so a language switch re-renders the tile, and keyed
    # below so the raw rows are rebuilt in the new language.
    locale = current_locale()

    def _sync_table():
        counts = (calculate_alert_classes(gdf, "Confirmed", "False Positive", "Need revision")
                  if has_alerts else [0, 0, 0, 0, 0])
        tbody_ref.children = _make_summary_rows(counts, [t(k) for k in _ALERT_LABEL_KEYS])

    solara.use_effect(_sync_table, [gdf, locale])

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
                pm = set_store.ensure_pmtiles(project, alert_set, on_wait=_defer)
                once = (project.name, alert_set.id)
                warning = None if pm is not None or once in warned.current else t("steps.integrate.no_tippecanoe")
                if pm is None:
                    warned.current.add(once)
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
                pm, warning = None, t("steps.overview.tiles_failed", error=error_text(exc))
            if not _still_open():
                return
            if pm is not None:
                try:
                    add_set_pmtiles(map_, pm, key=SET_LAYER_KEY, name=name)
                    return
                except Exception as exc:  # noqa: BLE001 - the centroids stand in for the tiles
                    logger.exception("could not show the tiles of set %s", alert_set.id)
                    warning = t("steps.overview.tiles_failed", error=error_text(exc))
            if warning is not None:
                notifications.warning(warning)
            _add_centroid_layer(map_, alerts, name)

        # spec §9.4: the tiles and the centroids draw the same status colours
        layers.show(SET_LAYER_KEY, build, content_id=f"{alert_set.id}:{len(alerts)}", notifier=notifications,
                    legend=set_legend(SET_LAYER_KEY, alert_set))

    solara.use_effect(_sync_layer, [active, s.id if s else None, gdf, retries])

    show_earlier, set_show_earlier = solara.use_state(False)
    # (project, set id, set fingerprint) -> the alerts an earlier set's build
    # loaded, for the hit test. Written by the builds (MapLayers workers), read
    # by the click handler: keyed so that alerts of another project (set ids
    # repeat across projects) or of a rebuilt set are never hit-tested.
    earlier_alerts = solara.use_ref({})
    # Earlier-set keys whose tiles were not built because the project was
    # being written (often by a sibling's build): refreshed once it is free.
    earlier_deferred = solara.use_ref(set())
    # Bumped by each such refresh: MapLayers.refresh rebuilds only a new content id.
    earlier_retry = solara.use_ref(0)
    earlier = earlier_of(p, s) if p is not None else []
    earlier_sig = tuple((e.id, e.fingerprint, e.n_reviewed) for e in earlier)

    def _earlier_build(project, e, key):
        name = earlier_set_name(e)

        def _defer():
            # As the active set's _defer: noted while the writer mark that
            # blocked the build is still held, before its release re-runs
            # _retry_earlier_when_free.
            earlier_deferred.current.add(key)

        def build():
            if app_state.panel_view.value != OVERVIEW_VIEW:
                return
            earlier_deferred.current.discard(key)
            alerts = set_store.load_set_alerts(project, e)
            if alerts is None:
                raise UserFileMissing(f"earlier set {name}: its store is missing", key="steps.overview.earlier_missing",
                                      set=name)
            earlier_alerts.current[(project.name, e.id, e.fingerprint)] = alerts
            pm = None
            if len(alerts):  # spec §9.2: an empty set has no tiles
                try:
                    pm = set_store.ensure_pmtiles(project, e, on_wait=_defer)
                except set_store.StoreBusy as exc:
                    # The project is being written (an Integrate, a Save, a
                    # sibling set's tile build): the points stand in, silently,
                    # and the tiles are built once the writer is done.
                    logger.info("tiles of earlier set %s not rebuilt now: %s", e.id, exc)
                    _defer()
                except Exception:  # noqa: BLE001 - the points stand in for the tiles
                    logger.exception("could not build the tiles of earlier set %s", e.id)
            if app_state.panel_view.value != OVERVIEW_VIEW:
                return
            if pm is not None:
                add_set_pmtiles(map_, pm, key=key, name=name, faded=True)
            else:
                _add_centroid_layer(map_, alerts, name, key=key, faded=True)

        return build

    def _sync_earlier():
        wanted = {f"{EARLIER_PREFIX}{e.id}": e for e in earlier} if active and show_earlier else {}
        for key in sorted(layers.on.value):
            if key.startswith(EARLIER_PREFIX) and key not in wanted:
                layers.hide(key)
        # Alerts of sets no longer drawn are let go (a show below reloads its own).
        keep = {(p.name, e.id, e.fingerprint) for e in wanted.values()}
        for loaded in list(earlier_alerts.current):
            if loaded not in keep:
                earlier_alerts.current.pop(loaded, None)
        for key, e in wanted.items():
            layers.show(key, _earlier_build(p, e, key), content_id=f"{e.id}:{e.fingerprint}:{e.n_reviewed}",
                        notifier=notifications, legend=earlier_set_legend(key, e))

    solara.use_effect(_sync_earlier, [active, show_earlier, p.name if p is not None else None, earlier_sig])

    def _retry_earlier_when_free():
        # spec §9.2: tiles skipped while the project was being written are
        # built once the writer is done. Only the deferred keys are rebuilt,
        # through ``refresh`` (a key switched off meanwhile stays off).
        if busy or not earlier_deferred.current:
            return
        by_key = {f"{EARLIER_PREFIX}{e.id}": e for e in earlier}
        for key in sorted(earlier_deferred.current):
            earlier_deferred.current.discard(key)
            e = by_key.get(key)
            if e is None:
                continue
            earlier_retry.current += 1
            layers.refresh(key, _earlier_build(p, e, key),
                           f"{e.id}:{e.fingerprint}:{e.n_reviewed}:retry{earlier_retry.current}",
                           legend=earlier_set_legend(key, e), notifier=notifications)

    solara.use_effect(_retry_earlier_when_free, [busy])

    def _on_install():
        # The shell clears every layer when a project is installed (spec
        # §4.3): the switch goes off with them rather than reading on over
        # nothing, whichever of the two effects runs first.
        earlier_alerts.current.clear()
        set_show_earlier(False)

    solara.use_effect(_on_install, [app_state.project_loaded_signal.value])

    def _close_popup():
        if popup_ref.current is not None:
            map_.remove_layer(popup_ref.current, none_ok=True)
            popup_ref.current = None

    def _hit(project, current, lon, lat):
        """(set, alerts, row position) of the alert under the click, or None:
        the active set's alerts first, then the earlier sets shown, newest
        first."""
        s_now = active_set(project)
        i = set_store.hit_alert(current, lon, lat)
        if s_now is not None and i is not None:
            return s_now, current, i
        for e in earlier_of(project, s_now):
            alerts = earlier_alerts.current.get((project.name, e.id, e.fingerprint))
            if alerts is None or not layers.is_on(f"{EARLIER_PREFIX}{e.id}"):
                continue
            i = set_store.hit_alert(alerts, lon, lat)
            if i is not None:
                return e, alerts, i
        return None

    def _on_map(**kwargs):
        if kwargs.get("type") != "click" or app_state.panel_view.value != OVERVIEW_VIEW:
            return
        lat, lon = kwargs["coordinates"]
        project = app_state.project.value
        hit = _hit(project, _set_alerts(project, app_state.alerts_gdf.value), lon, lat) if project else None
        _close_popup()
        if hit is None:
            return
        target, alerts, i = hit
        row = alerts.iloc[i]
        alert_id = int(row.get("alert_id", i))
        go = v.Btn(small=True, color="primary", children=[t("steps.overview.go")])
        go.on_event("click", lambda *_: set_store.go_to_alert(app_state, project.name, target.id, alert_id, i))
        summary = t("steps.overview.popup", id=alert_id, status=row.get("status", ""),
                    area=format_number(float(row.get('area_ha', 0)), 2))
        lines = [v.Html(tag="div", children=[summary])]
        if target.id != project.active_set:
            lines.append(v.Html(tag="div", children=[earlier_set_name(target)]))
        popup = Popup(location=(lat, lon), child=v.Html(tag="div", children=[*lines, go]), close_button=True,
                      auto_close=True)
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
            if earlier:
                with solara.Div(style="padding: 0 16px;"):
                    rv.Switch(label=t("steps.overview.show_earlier"), v_model=show_earlier,
                              on_v_model=set_show_earlier, dense=True, hide_details=True)
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
