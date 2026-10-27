"""The review Save (spec §10): what one alert's review writes, and the
store transaction that writes it. Data and I/O only, no widgets: the save
runner (``export.make_save_runner``) calls ``save_review`` on a worker.

The Save writes the store of the set that was active when it started, and
only while that set is still the active one and its store on disk is still
the one the alerts came from; anything else is a ``SaveRefused``, which
writes nothing.
"""

import ee

from deforestation_alerts.errors import KeyedError
import pandas as pd

from deforestation_alerts.project import paths, review_groups
from deforestation_alerts.project.growth import parse_link
from deforestation_alerts.project.questions import column
from deforestation_alerts.project.review_status import FALSE_POSITIVE, VERDICTS
from deforestation_alerts.project.store_schema import GROWS_FROM
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from gui.scripts import growth_index, questions
from gui.scripts import set_store


def save_target(app_tile_model):
    """Where a Save writes, read once: ``(folder, target)``.

    A ``SetContext`` pins its active set (``target``: that set and its
    project) and the folder is that set's. A recipe-era model (the old shell,
    test fakes) has only ``recipe_folder_path``, and no target. The folder is
    ``""`` when there is nowhere to write.
    """
    pin = getattr(app_tile_model, "pin", None)
    if pin is None:
        return app_tile_model.recipe_folder_path, None
    target = pin()
    return (str(target.folder), target) if target is not None else ("", None)


class SaveRefused(KeyedError):
    """A Save that must not write: nothing went wrong, it has nowhere to go.
    The worker reports it as a warning, not as an error. ``key`` names the
    reason; the worker renders it where it toasts (spec §11)."""


#: A Save refused for a required question left unanswered (spec D5); the
#: refusal carries the question's label (``question``) and key (``question_key``).
REQUIRED_MISSING = "analysis_tile.questions.required_missing"


def refuse_unless_current(alertas_gdf, app_tile_model, target) -> None:
    """Raise ``SaveRefused`` unless the Save may still write for ``target``.

    ``alerts_gdf`` and the active set are separate reactives: Integrate records
    a new set before it publishes that set's alerts, so for a moment the alerts
    on hand are the previous set's. Saved, they would overwrite the new set's
    store. And the admin lookups block for seconds, during which an Integrate
    can activate another set, or the project can be switched: the alerts this
    Save edited then belong to a set that is no longer the open project's
    active set, so they are not written at all (Review Focus 5).

    A re-run of the *same* set keeps its id, so those two checks pass while
    the run swaps a new store in under the alerts on hand. The alerts carry
    the generation of the store they were read from
    (``set_store.stamp_generation``): once the store on disk is another
    generation, writing them back would overwrite the new set, so the Save is
    refused (``save_store_changed``). Checked before the lookups, and again
    right before the file is moved into place (``write_store``).
    """
    if target is None:
        return
    if not set_store.alerts_belong_to(alertas_gdf, target.alert_set):
        raise SaveRefused(key="analysis_tile.export_labels.save_other_set")
    if not app_tile_model.is_current(target):
        raise SaveRefused(key="analysis_tile.export_labels.save_set_changed")
    if set_store.generation_changed(alertas_gdf, paths.store_path(str(target.folder))):
        raise SaveRefused(key="analysis_tile.export_labels.save_store_changed")


def kept_links(alert, app_tile_model, target) -> list[str]:
    """The draft links a Save stores: those that parse and still resolve.
    A link to a re-run set or a missing alert is dropped (spec §5.3); one to
    a parent that has since become a False Positive is kept (Ruling 13: greyed,
    out of the totals, as on open). Reads the other sets' stores, so it runs
    on the save worker. Without a project to resolve them against only the
    parse is checked, and so in a review copy whose package has no snapshot:
    nothing earlier resolves there, and the open keeps them as they are
    (``earlier._kept_links``)."""
    draft = [link for link in (getattr(alert, "links", None) or ()) if parse_link(link) is not None]
    state = getattr(app_tile_model, "app_state", None)
    p = state.project.value if state is not None else None
    if not draft or p is None or target is None:
        return draft
    rc = getattr(p, "review_copy", None)
    if rc is not None and getattr(rc, "snapshot_at", None) is None:
        return draft
    graph = growth_index.graph_for(p, target.alert_set.id, alert.alerts)
    return [link for link in draft if not graph.is_broken(link)]


def _put_links(gdf, alert_id, links) -> None:
    """Write one row's ``grows_from``. The column is object dtype: a store
    whose Integrate linked nothing has it as ``list<null>``, which reads back
    as empty arrays and cannot take a list of strings."""
    if GROWS_FROM not in gdf.columns:
        gdf[GROWS_FROM] = pd.Series([[] for _ in range(len(gdf))], index=gdf.index, dtype="object")
    elif gdf[GROWS_FROM].dtype != object:
        gdf[GROWS_FROM] = gdf[GROWS_FROM].astype(object)
    gdf.at[alert_id, GROWS_FROM] = list(links)


def _refuse_evaluators(gdf, plan, alert) -> None:
    """A group belongs to one evaluator (spec §8.2): refuse a plan over rows
    of several, and, in a review copy, one that touches rows not assigned
    to the copy's evaluator."""
    if review_groups.mixed_evaluators(gdf, plan):
        raise SaveRefused(key="analysis_tile.nearby.mixed_evaluators")
    evaluator = getattr(alert, "evaluator", None)
    if evaluator and review_groups.foreign_rows(gdf, plan, evaluator):
        raise SaveRefused(key="analysis_tile.nearby.foreign_rows")


def _write_cells(gdf, updates) -> None:
    """Write ``{position: {column: value}}`` into ``gdf`` in place;
    ``grows_from`` goes through ``_put_links`` (a list cannot be set with
    ``.at`` into an object column)."""
    for position, cells in updates.items():
        for column_name, value in cells.items():
            if column_name == GROWS_FROM:
                _put_links(gdf, position, value)
            else:
                gdf.at[position, column_name] = value


def _write_plan(alertas_gdf, updates, answer_columns, app_tile_model, folder, target) -> None:
    """The plan's cells into a private copy, ONE store write, then the same
    cells into the live table (spec §5.2). ``review_group`` is made an
    ``Int64`` column on both tables before any cell is set: a column born
    from ``.at`` infers ``float``/``object``, and the next staged copy would
    persist that."""
    staged = alertas_gdf.copy()
    review_groups.ensure_column(staged)
    for column_name in answer_columns:
        if column_name not in staged.columns:
            staged[column_name] = ""  # a question added after this set was made
    _write_cells(staged, updates)
    write_store(staged, app_tile_model, folder=folder, target=target)

    review_groups.ensure_column(alertas_gdf)
    for column_name in answer_columns:
        if column_name not in alertas_gdf.columns:
            alertas_gdf[column_name] = ""
    _write_cells(alertas_gdf, updates)
    alertas_gdf.attrs.update(staged.attrs)


def review_status(confirmation) -> str:
    """The status a Save stores (spec §10): one of the three, never a label.

    Anything else is refused, an unhashable value (a list) included."""
    if not isinstance(confirmation, str) or confirmation not in VERDICTS:
        raise SaveRefused(key="analysis_tile.questionarie.confirmation_missing")
    return confirmation


def save_review(alert, app_tile_model):
    """Write the review of ``alert`` (the OLD ``save_attributes_to_gdf``; data/IO only).

    Prepares every field of the review, persists ``alert_db.parquet`` from a
    private copy of the table, and only then writes the row of
    ``app_state.alerts_gdf.value`` in place via ``.at[id, col] = ...``
    (faithful to OLD: that object is what Overview and the map layers read).
    A refusal or a failure therefore leaves the live table exactly as it was.
    Returns ``True`` on success (the caller stamps ``last_save_time``: see
    ``export.make_save_runner``).
    Guards on the gdf being present + the id in range (smoke-safe). Raises
    ``SaveRefused`` for the OLD "False Positive but a polygon is drawn" guard
    (``false_positive_drawn``), a missing answer, a drawing the reviewer has
    not applied (``unapplied_drawing``), and alerts of another set than the one
    it writes for.

    The store it writes is the active set's AS THE SAVE STARTS: the admin
    lookups below block for seconds, and the live ``SetContext`` (ruling #84)
    follows an Integrate that activates a new set, or a project switch,
    meanwhile. The write then happens only if that set is still the active
    one (``write_store``); otherwise the Save is refused.

    ``alert`` (``state.capture_alert``) is what the Save writes: the alert, its
    images, drawn polygon and questionnaire, read together at the click. An
    alert change while the lookups block leaves them alone; the row written
    is the clicked alert's, with its own values.

    With grouping effective the capture carries a plan (``alert.plan``,
    spec 2026-10-26 §5.2): the same refusals, one admin lookup over the
    group's bounds, the evaluator refusals, then every row of the plan in
    one store write (``_write_plan``). Without one this is the single save.
    """
    plan = getattr(alert, "plan", None)
    if plan is not None and plan.kind == review_groups.REMOVE:
        return remove_from_group(alert, app_tile_model)
    folder, target = save_target(app_tile_model)
    alertas_gdf = alert.alerts
    actual_alert_id = alert.alert_id

    # Guard: nothing to save without an alert set / valid id (smoke-safe).
    if alertas_gdf is None or actual_alert_id is None or actual_alert_id < 0:
        return False
    if actual_alert_id >= len(alertas_gdf):
        return False
    # Before any row changes or a lookup blocks; the store writer checks again.
    refuse_unless_current(alertas_gdf, app_tile_model, target)
    status = review_status(alert.status)

    # A drawing with changes *Apply* has not written yet: ``defo_layer`` is the
    # polygon from BEFORE them, so this save would store that one over work the
    # reviewer can still see on the maps -- and Save & next would navigate it
    # away. OLD disabled the review Save for the whole edit session
    # (``start_edition_function``); this is the same guard, as a message that
    # names what to do. Captured at the click like everything else here.
    if getattr(alert, "drawing_unapplied", False):
        raise SaveRefused(key="analysis_tile.edition_labels.unapplied_drawing")

    # One column per current question (spec D2, D5). A question not asked for
    # this verdict stores "" -- a false positive records no loss driver -- and
    # its draft stays in the panel, so switching the verdict back still has
    # it. A required question is required only where it is asked. Drafts of
    # questions deleted since are ignored: their columns are left as stored.
    # Refused here, before the admin lookups block and before any row changes.
    answer_updates = {}
    for q in getattr(alert, "questions", None) or []:
        shown = questions.visible(q, status)
        value = questions.encode(q, (getattr(alert, "answers", None) or {}).get(q.key)) if shown else ""
        if shown and q.required and not value:
            raise SaveRefused(key=REQUIRED_MISSING, question=questions.question_label(q), question_key=q.key)
        answer_updates[column(q)] = value

    # Every field this save writes, prepared BEFORE the live table is touched:
    # the refusals below and the admin lookups come between, and a row already
    # half-written by a save that is then refused (or that fails) is a review
    # the reviewer was told was rejected, sitting in the table Overview counts
    # from and the next save persists whole. Applied in one pass at the end,
    # once the store write has landed.
    updates = {"status": status, **answer_updates}
    links = kept_links(alert, app_tile_model, target)

    # before/after image name + info. Guarded (fix round 1): both are None
    # until an image is actually picked for this alert (``AnalysisState``;
    # ``reset_alert`` clears them on every alert change), e.g. no Planet login
    # yet and no S2/Landsat chosen; unconditionally subscripting raised
    # "'NoneType' object is not subscriptable" and lost the whole review, the
    # same way the drawn-polygon case below is guarded on
    # ``defo_layer is None``.
    before_info = alert.before_info
    after_info = alert.after_info
    # Captured with the images: an alert change meanwhile resets it
    # (``reset_alert``), or the next alert's drawn polygon replaces it, and
    # neither belongs to the alert this Save writes.
    defo_layer = alert.defo_layer
    updates["before_img"] = None if before_info is None else before_info[3]
    updates["before_img_info"] = None if before_info is None else before_info[1]
    updates["after_img"] = None if after_info is None else after_info[3]
    updates["after_img_info"] = None if after_info is None else after_info[1]

    # Administrative location attributes via GEE FAO/GAUL (OLD 1799-1828).
    # A group save looks over the union bounds of its main and members, still
    # in one lookup (spec §5.2).
    adminL2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
    if plan is None:
        where = alert.bb.geometry()
    else:
        where = ee.Geometry.Rectangle(list(review_groups.bounds(alertas_gdf, (plan.main, *plan.members))))
    selected_admin = adminL2.filterBounds(where)
    # Joined server-side: the OLD iterate appended ", " after every name and
    # sliced one character off, which left a trailing comma ("Paraguay,").
    names = ee.Dictionary({
        column: selected_admin.aggregate_array(field).distinct().join(", ")
        for column, field in (("admin1", "ADM0_NAME"), ("admin2", "ADM1_NAME"),
                              ("admin3", "ADM2_NAME"))
    }).getInfo()  # one round trip, not three
    updates.update(names)

    # OLD guard (1830): "False Positive" but a polygon was drawn => inconsistent.
    # A refusal: nothing is written at all -- not the store, and not the row
    # (the admin names above are in ``updates``, which is dropped with it).
    if (
        status == FALSE_POSITIVE
        and defo_layer is not None
    ):
        raise SaveRefused(key="analysis_tile.questionarie.false_positive_drawn")

    drawing = None if defo_layer is None else defo_layer["geometry"].union_all()

    if plan is not None:
        # A group save (spec §4.3, §5.2): one evaluator per group, then every
        # cell of the plan -- the main, its members, the rows it detaches --
        # in one store write.
        _refuse_evaluators(alertas_gdf, plan, alert)
        cells = review_groups.plan_updates(alertas_gdf, plan, updates, drawing=drawing,
                                           crs=alert.area_crs, links=links)
        _write_plan(alertas_gdf, cells, answer_updates, app_tile_model, folder, target)
        return True

    # alert_polygon + area_ha from the drawn defo layer (if any), measured in
    # the project's grid CRS like Integrate's: the area of the union that is
    # stored, so overlapping features are not counted twice (spec §4.3). With
    # nothing drawn, area_ha is the alert's Integrate area -- not what a save
    # with a drawing left in the row. The store feeds the exports, the map
    # tiles and the report.
    updates["alert_polygon"] = drawing
    if drawing is None:
        updates["area_ha"] = review_groups.own_area(alertas_gdf, actual_alert_id, alert.area_crs)
    else:
        updates["area_ha"] = review_groups.polygon_area_ha(
            drawing, alert.area_crs, source=defo_layer.crs or "EPSG:4326")

    # Persist the alert DB (OLD save_alerts_to_gdf) where the save started,
    # from a private copy carrying this review. Skip if there is no folder.
    #
    # The copy is what makes a FAILED write (a full disk, a set deleted
    # mid-write) leave the app as it was: the live table is published only
    # below, once the parquet has landed, so the app never shows a review as
    # stored that is not on disk. ``_default_apply`` re-publishes a copy of
    # the live table after every save anyway, so this is the same order of
    # cost.
    staged = alertas_gdf.copy()
    # Only the answer columns are padded: under pandas' string inference a
    # column made from "" is a str column, and a float (``area_ha``) set into
    # it raises. Any other missing column is left to ``.at``'s enlargement.
    for column_name in answer_updates:
        if column_name not in staged.columns:
            staged[column_name] = ""  # a question added after this set was made
    for column_name, value in updates.items():
        staged.at[actual_alert_id, column_name] = value
    _put_links(staged, actual_alert_id, links)
    write_store(staged, app_tile_model, folder=folder, target=target)

    # Published to the live table (in place, faithful to OLD: Overview and the
    # map layers read this very object, and the save runner re-publishes it).
    # A new question's column is added first, "" on every other row, as above.
    for column_name in answer_updates:
        if column_name not in alertas_gdf.columns:
            alertas_gdf[column_name] = ""
    for column_name, value in updates.items():
        alertas_gdf.at[actual_alert_id, column_name] = value
    _put_links(alertas_gdf, actual_alert_id, links)
    # With the store generation the write stamped on the copy
    # (``set_store.stamp_generation``): the alerts on hand ARE what is on disk
    # now, and without the stamp the live table still claims the generation
    # from before this write -- which the next Save of the same alerts reads
    # as "the store changed under us" and refuses.
    alertas_gdf.attrs.update(staged.attrs)

    return True


def remove_from_group(alert, app_tile_model) -> bool:
    """*Remove from group* (spec §4.4, §5.4): write ``alert.plan`` (a REMOVE
    plan, ``state.capture_alert(..., remove=True)``), which resets the
    removed member to unreviewed and leaves the rest of its group as it is.
    The refusals that apply -- another set, the set changed, the store
    changed, the evaluators -- then one staged store write and the live
    table. No Earth Engine call. Returns ``True`` once written."""
    folder, target = save_target(app_tile_model)
    alertas_gdf = alert.alerts
    plan = getattr(alert, "plan", None)
    if alertas_gdf is None or plan is None or plan.kind != review_groups.REMOVE:
        return False
    if not all(0 <= p < len(alertas_gdf) for p in plan.positions()):
        return False
    refuse_unless_current(alertas_gdf, app_tile_model, target)
    _refuse_evaluators(alertas_gdf, plan, alert)
    cells = review_groups.plan_updates(alertas_gdf, plan, crs=alert.area_crs)
    _write_plan(alertas_gdf, cells, (), app_tile_model, folder, target)
    return True


def write_store(alertas_gdf, app_tile_model, *, folder=None, target=None):
    """Persist the alerts gdf to the canonical GeoParquet store.

    Writes ``<folder>/alert_db.parquet`` (all geometry columns preserved)
    through a unique ``.part`` renamed into place. Then records the set's
    reviewed count (``record_review``, when the model has one).

    ``folder`` and ``target`` are where the Save started (``save_target``);
    without ``folder`` both are read now. Raises ``SaveRefused``, writing
    nothing, unless the alerts are ``target``'s set's, that set is still the
    open project's active set, and its store on disk is still the one the
    alerts came from; the last check runs again right before the serialized
    file is moved into place, so a store swapped in during the serialization
    is not overwritten either. After the write the alerts are stamped with
    the new store's generation, so the next Save of them lands.
    Guard: skip if the folder is empty (smoke / no set yet).
    """
    if folder is None:
        folder, target = save_target(app_tile_model)
    if not folder:
        return
    refuse_unless_current(alertas_gdf, app_tile_model, target)
    store = str(paths.store_path(folder))
    save_gdf_to_parquet(
        alertas_gdf,
        store,
        guard=lambda: refuse_unless_current(alertas_gdf, app_tile_model, target),
    )
    if target is not None:
        set_store.stamp_generation(alertas_gdf, store)
    record = getattr(app_tile_model, "record_review", None)
    if record is not None:
        # The count of the set this store belongs to (the Analysis badge).
        record(alertas_gdf, target)
