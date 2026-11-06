"""The Overview's export card (spec 2026-10-15 overview-export): which alerts
(the scope chips) and which file (one ``FileTile`` per format). Each tile
finds the file an earlier session wrote and says when it is older than the
set's store.

Architecture layer: view. A RAW ipyvuetify tree, built once per set by the
Overview's ``use_memo`` and relabelled in place on a language switch: MapApp
rebuilds its dialogs with ``v-if``, and a plain widget tree is re-viewed from
its model, so the card survives a reopen; and the widgets a user clicks are
the ones the tests drive.

Each tile has four faces. Create: no file for this scope yet, the tile is a
button that writes it. Creating: the write runs, the tile spins. Ready: the
whole tile is the file's download link. Out of date: still the link, its
detail led by a warning, with an Update button under it that rewrites the
file. A face is read off the disk (``_face``), so a failed rewrite shows the
old file again rather than an empty tile.

Threading + feedback: writing a GeoPackage is blocking IO, so a write runs on
a daemon thread through ``spawn_in_context`` under ``run_button_job``, like
``analysis/export``: the worker resolves its notifier from its kernel context
(``notify_bridge.current_notifier``), and a failure is a FAILED task and one
error toast. The scope chips are locked while any write runs, so a file never
lands under a scope it was not made for.
"""

import logging
from datetime import datetime
from pathlib import Path

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.project.review_status import CONFIRMED, NOT_REVIEWED, STATUS_CODES
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_export import (
    EXPORT_FORMATS, SCOPES, export_alerts_gdf, export_name, scope_counts, scope_rows,
)
from gui.i18n import format_number, t, tn
from gui.scripts import questions as questions_mod
from gui.scripts.error_text import error_text
from gui.scripts.legend_specs import status_colors
from gui.scripts.notify_bridge import current_notifier, run_button_job
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writing
from gui.tile.analysis.file_tile import FileTile
from gui.widget.copy_chip import CopyChipWidget

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")

#: The tiles, in order: GeoPackage for GIS, CSV for spreadsheets.
FORMATS = tuple(EXPORT_FORMATS)
#: The scopes whose chip carries its status's legend colour.
_DOT = {"confirmed": CONFIRMED, "unreviewed": NOT_REVIEWED}
_TITLE = "font-size: 0.82rem; font-weight: 600; letter-spacing: 0.02em;"
_NOTE = "font-size: 0.72rem; opacity: 0.72; line-height: 1.25;"


class ExportCard(v.Card):
    """The card. Its parts are attributes (``tiles``, ``update_btns``,
    ``chips``, ``scope_group``, ``folder_chip``) so the Overview and the tests
    reach the widgets a user clicks without indexing children.

    ``store`` is the set's alert store: a file older than it is out of date.
    ``project`` is the name of the project ``folder`` belongs to, marked as
    being written while a file is (spec §12), so a delete is refused instead
    of the export re-creating the deleted folder. ``scope`` is the one to
    start on (the previous card's, kept across a rebuild) when it has alerts;
    otherwise Confirmed when it has some, else All. ``growth``, when given, maps
    the alerts to the rows to export with the growth columns
    (``growth_index.with_growth_columns``); it reads the project's stores, so
    it runs on the write's worker."""

    def __init__(self, gdf, folder, sources, start, end, project, questions=(), store=None, scope=None,
                 growth=None):
        self._growth = growth
        self._gdf, self._folder, self._sources = gdf, str(folder), list(sources)
        self._start, self._end, self._project = start, end, project
        self._questions, self._store = tuple(questions), store
        self._counts = scope_counts(gdf)
        #: The formats being written right now, and the scope they are written for.
        self.busy = set()
        self._writing_scope = None

        self._title = v.Html(tag="div", style_=_TITLE, children=[""])
        self._summary = v.Html(tag="div", style_=_NOTE, children=[""])
        self.folder_chip = CopyChipWidget(text=_shown_folder(self._folder))
        self._scope_label = v.Html(tag="span", style_=_NOTE + "margin-right: 8px;", children=[""])
        colors = status_colors()
        self.chips = {
            scope: v.Chip(small=True, outlined=True, filter=True, value=scope,
                          disabled=not self._counts[scope], children=self._chip_children(scope, colors))
            for scope in SCOPES
        }
        default = "confirmed" if self._counts["confirmed"] else "all"
        if scope in SCOPES and self._counts[scope]:
            default = scope
        self.scope_group = v.ChipGroup(mandatory=True, v_model=default, active_class="primary--text",
                                       column=True, children=list(self.chips.values()))

        self.tiles, self.update_btns, columns = {}, {}, []
        for fmt in FORMATS:
            tile = FileTile(fmt, "", "")
            btn = sw.Btn(msg="", gliph="mdi-refresh", small=True, text=True, color="warning").hide()
            tile.on_event("click", self._on_create(fmt))
            btn.on_event("click", self._on_update(fmt))
            self.tiles[fmt], self.update_btns[fmt] = tile, btn
            columns.append(v.Html(tag="div", style_="flex: 1 1 260px; min-width: 0;", children=[
                tile, v.Html(tag="div", class_="d-flex justify-end", children=[btn])]))
        self._footer = v.Html(tag="div", style_=_NOTE + "font-family: monospace;", children=[""])
        self.scope_group.observe(self._on_scope, "v_model")

        super().__init__(outlined=True, class_="pa-3", children=[
            v.Html(tag="div", class_="d-flex align-center flex-wrap", style_="gap: 4px 12px;",
                   children=[self._title, self._summary, v.Spacer(), self.folder_chip]),
            v.Html(tag="div", class_="d-flex align-center flex-wrap",
                   children=[self._scope_label, self.scope_group]),
            v.Html(tag="div", class_="d-flex flex-wrap", style_="gap: 0 8px;", children=columns),
            self._footer,
        ])
        self.relabel()

    @property
    def scope(self):
        """The scope the chips have picked."""
        return self.scope_group.v_model

    # -- building blocks ----------------------------------------------------
    def _chip_children(self, scope, colors):
        """[legend dot,] label (written by ``relabel``), count."""
        dot = []
        if scope in _DOT:
            color = colors[STATUS_CODES[_DOT[scope]]]
            dot = [v.Html(tag="span", style_=f"width: 8px; height: 8px; border-radius: 50%; "
                                             f"margin-right: 6px; background: {color};")]
        return dot + [v.Html(tag="span", children=[""]),
                      v.Html(tag="span", style_="opacity: 0.65; margin-left: 6px;",
                             children=[format_number(self._counts[scope])])]

    def _path(self, fmt):
        return Path(self._folder) / export_name(self._sources, self._start, self._end, fmt, self.scope)

    def _stale(self, path):
        """Whether ``path`` is older than the set's store (a review saved since)."""
        if self._store is None:
            return False
        try:
            return path.stat().st_mtime < Path(self._store).stat().st_mtime
        except OSError:
            return False

    def _what(self, fmt):
        n = format_number(self._counts[self.scope])
        return t("overview_tile.export_polygons" if fmt == "gpkg" else "overview_tile.export_rows", n=n)

    def _face(self, fmt):
        """Put the tile and its Update button in the face its file on disk calls for."""
        if fmt in self.busy:
            return
        tile, btn = self.tiles[fmt], self.update_btns[fmt]
        path = self._path(fmt)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is None:
            tile.set_file(None)
            tile.set_action(True, self._what(fmt), icon="mdi-file-plus-outline")
            btn.hide()
            return
        stale = self._stale(path)
        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        try:
            tile.set_file(str(path), detail=t("overview_tile.export_written", what=self._what(fmt), when=when),
                          warning=t("overview_tile.export_stale") if stale else None)
        except ValueError as exc:  # NotDownloadable: a link to it would 404
            tile.set_file(None)
            tile.set_action(False, error_text(exc), icon="mdi-link-off")
            btn.hide()
            return
        btn.show() if stale else btn.hide()

    def _lock(self, locked):
        for scope, chip in self.chips.items():
            chip.disabled = locked or not self._counts[scope]

    # -- labels -------------------------------------------------------------
    def relabel(self):
        """Every label in the current language, and every tile in the face its
        file calls for. The scope, the files and a running write are kept."""
        self._title.children = [t("overview_tile.export_label")]
        rows = scope_rows(self._gdf, self.scope)
        area = float(rows["area_ha"].sum()) if "area_ha" in rows.columns else 0.0
        self._summary.children = [t("overview_tile.export_summary", n=format_number(len(rows)),
                                    area=format_number(area, 1), sources=" + ".join(self._sources),
                                    start=self._start, end=self._end)]
        self._scope_label.children = [t("overview_tile.export_scope")]
        for scope, chip in self.chips.items():
            chip.children[-2].children = [t(f"overview_tile.export_scope_{scope}")]
        for fmt in FORMATS:
            self.tiles[fmt].set_title(t(f"overview_tile.export_{fmt}"))
            self.update_btns[fmt].msg = t("overview_tile.export_update")
            self._face(fmt)
        stem = Path(export_name(self._sources, self._start, self._end, "csv", self.scope)).stem
        self._footer.children = [t("overview_tile.export_footer", name=stem)]
        return self

    # -- events -------------------------------------------------------------
    def _on_scope(self, change):
        if self.busy:  # a change that slipped past the locked chips is undone
            if change["new"] != self._writing_scope:
                self.scope_group.v_model = self._writing_scope
            return
        self.relabel()

    def _on_create(self, fmt):
        def _click(widget, event, data):
            tile = self.tiles[fmt]
            # A tile with a file is a link: the browser downloads it.
            if fmt in self.busy or tile.path is not None or tile.disabled:
                return
            self._write(fmt)
        return _click

    def _on_update(self, fmt):
        def _click(widget, event, data):
            if fmt not in self.busy:
                self._write(fmt)
        return _click

    def _write(self, fmt):
        """Write the ``fmt`` file of the current scope on a worker."""
        tile, scope = self.tiles[fmt], self.scope
        n = format_number(self._counts[scope])
        self.busy.add(fmt)
        self._writing_scope = scope
        self._lock(True)
        self.update_btns[fmt].hide()
        tile.set_file(None)
        tile.set_action(False, t("overview_tile.export_writing", n=n), icon="mdi-progress-upload")
        tile.loading = True
        gdf, questions, growth = self._gdf, self._questions, self._growth

        # One opaque phase (the write), so no ``total_steps``. A failure is
        # tracked_job's: a FAILED task and the one error toast.
        def _body(task, notifier):
            task.step(t("overview_tile.export_step", fmt=fmt.upper()))
            with writing(self._project):
                rows = gdf if growth is None else growth(gdf)
                # the answers in the export's language (the store keeps their
                # codes, see gui.scripts.questions); the columns of deleted
                # questions are left out
                path = export_alerts_gdf(questions_mod.with_labels(rows, list(questions)), self._folder, fmt,
                                         self._sources, self._start, self._end, scope=scope)
            # A file the server cannot offer is a failed export, said in the
            # task, not a tile that links nowhere (NotDownloadable).
            local_files.served_relative(path)
            return None

        def _done(notifier):
            notifier.success(t("overview_tile.export_done", fmt=fmt.upper()))

        def _release():
            # Runs last, whatever happened: the face is read off the disk, so
            # it shows the new file, or the old one again after a failure.
            tile.loading = False
            self.busy.discard(fmt)
            self._lock(bool(self.busy))
            self._face(fmt)

        run_button_job(
            t("overview_tile.export_task_title"), _body,
            release=_release, on_success=_done, logger=logger,
            spawn=spawn_in_context, notifier=current_notifier,
        )


def _shown_folder(folder):
    """The set folder as the download root sees it, or its absolute path when
    the server does not offer it (the copy still helps a terminal user)."""
    try:
        return local_files.served_relative(folder)
    except ValueError:
        return str(folder)


def build_export_card(gdf, folder, sources, start, end, project, questions=(), store=None, scope=None,
                      growth=None):
    """The export card of one set (see ``ExportCard``)."""
    return ExportCard(gdf, folder, sources, start, end, project, questions, store, scope, growth)


def relabel_export_card(card):
    """Write the card's labels in the current language, in place: the card is
    built once per set, so a language switch relabels it rather than
    rebuilding it (which would lose a write in progress)."""
    return card.relabel()


class SummaryFile(v.Html):
    """The Files tab's *Summary CSV* tile: ``text`` (``overview_data.summary_csv``)
    written to ``path`` on a click. The tile reads the file on disk: absent, it
    is the button that writes it; the same text, the download link; other text
    (reviews saved since), the link with a warning and an Update button. Built
    per text by the Overview's ``use_memo``. The write is a few hundred bytes,
    so it runs on the click, under ``writing(project)`` like every export."""

    def __init__(self, text, path, project, n_periods):
        self._text, self._path, self._project, self._n = text, Path(path), project, n_periods
        self.tile = FileTile("csv", "", "")
        self.update_btn = sw.Btn(msg="", gliph="mdi-refresh", small=True, text=True, color="warning").hide()
        self.tile.on_event("click", self._on_create)
        self.update_btn.on_event("click", lambda *_: self._write())
        super().__init__(tag="div", children=[
            self.tile, v.Html(tag="div", class_="d-flex justify-end", children=[self.update_btn])])
        self.relabel()

    def _what(self):
        return tn("overview_tile.summary_periods", self._n)

    def _write(self):
        try:
            with writing(self._project):
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(self._text, encoding="utf-8")
        except OSError as exc:
            logger.exception("could not write %s", self._path)
            self.tile.set_file(None)
            self.tile.set_action(False, error_text(exc), icon="mdi-alert-outline")
            return
        self._face()

    def _on_create(self, widget, event, data):
        # A tile with a file is a link: the browser downloads it.
        if self.tile.path is None and not self.tile.disabled:
            self._write()

    def _face(self):
        try:
            on_disk = self._path.read_text(encoding="utf-8")
            mtime = self._path.stat().st_mtime
        except OSError:
            on_disk = None
        if on_disk is None:
            self.tile.set_file(None)
            self.tile.set_action(True, t("overview_tile.summary_not_written", what=self._what()),
                                 icon="mdi-file-plus-outline")
            self.update_btn.hide()
            return
        stale = on_disk != self._text
        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        try:
            self.tile.set_file(str(self._path), detail=t("overview_tile.export_written", what=self._what(), when=when),
                               warning=t("overview_tile.summary_stale") if stale else None)
        except ValueError as exc:  # NotDownloadable: a link to it would 404
            self.tile.set_file(None)
            self.tile.set_action(False, error_text(exc), icon="mdi-link-off")
            self.update_btn.hide()
            return
        self.update_btn.show() if stale else self.update_btn.hide()

    def relabel(self):
        """Its labels in the current language, and the face its file calls for."""
        self.tile.set_title(t("overview_tile.summary_csv"))
        self.update_btn.msg = t("overview_tile.export_update")
        self._face()
        return self


def build_summary_file(text, path, project, n_periods):
    """The Summary CSV tile (see ``SummaryFile``)."""
    return SummaryFile(text, path, project, n_periods)
