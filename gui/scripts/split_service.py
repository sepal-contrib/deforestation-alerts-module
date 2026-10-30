"""Writing an evaluator split into the active set's store (spec §4.1, §4.4).
Solara-free: the Evaluators step calls these from a worker thread.

Both writers follow the store mutation path a review Save follows: under the
store's lock (``alert_store.store_lock``) they read the store stamped with its
generation, build the new frame, and write it with a guard that refuses when
the generation moved (a Save, a merge or another split landed first) or when
an Integrate job of the set is running (``integrate_jobs.is_integrating``: it
swaps a new store in and records a new set, which would drop the split).
Once the store has landed, the live alerts are republished with the new
generation (a Save of the old ones would erase the column) and the set's
``split`` record is published through ``SetContext._update_set``; the
auto-save writes the manifest. The store is the source of truth for the
column: a record lost after the store landed shows as ``orphan_assignments``.
"""

import uuid
from datetime import datetime

from deforestation_alerts.project import grouping, review_groups
from deforestation_alerts.project.models import EvaluatorSplit
from deforestation_alerts.project.review_status import is_reviewed
from deforestation_alerts.project.split import (
    EVALUATOR_COLUMN,
    SplitError,
    assignments,
    deal,
    normalize_names,
    oversized,
)
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet, store_lock
from gui.scripts import set_store
from gui.scripts.integrate_jobs import is_integrating
from gui.store.workflow_steps import active_set


def _attribute_clash(s) -> bool:
    """Whether one of the set's attribute layers writes an ``evaluator``
    column (made before the name was reserved, spec §3)."""
    return any(b.get("column") == EVALUATOR_COLUMN
               for layer in s.params.attribute_list() for b in (layer or {}).get("bands", []))


def _target(app_state, app_tile_model):
    """The open project and the active set's pinned target, or ``SplitError``."""
    p = app_state.project.value
    if p is not None and p.review_copy is not None:
        raise SplitError("review_copy.read_only", evaluator=p.review_copy.evaluator,
                         origin_project=p.review_copy.origin_project)
    target = app_tile_model.pin()
    if p is None or target is None:
        raise SplitError("evaluators.err.no_set")
    if _attribute_clash(target.alert_set):
        raise SplitError("evaluators.err.attribute_clash")
    if is_integrating(target.project, target.alert_set.id):
        raise SplitError("evaluators.err.integrating")
    return p, target


def _write(app_state, p, target, build):
    """Read the set's store under its lock, let ``build(gdf)`` make the new
    frame (None: nothing to write), write it guarded and republish it.
    Returns the frame written, or None."""
    store = set_store.store_path(p, target.alert_set)
    with store_lock(store):
        gdf = set_store.load_set_alerts(p, target.alert_set)  # stamped with its generation
        if gdf is None:
            raise SplitError("evaluators.err.no_set")
        staged = build(gdf)
        if staged is None:
            return None

        def _guard():
            if set_store.generation_changed(gdf, store):
                raise SplitError("evaluators.err.store_changed")
            if is_integrating(target.project, target.alert_set.id):
                raise SplitError("evaluators.err.integrating")

        save_gdf_to_parquet(staged, store, guard=_guard)
        set_store.stamp_generation(staged, store)
    _republish(app_state, target, staged)
    return staged


def _republish(app_state, target, gdf) -> None:
    """Hand the written alerts to Analysis while the set is still the open
    project's active set and the alerts on hand are its: the live table then
    carries the new column and the new generation, so the next Save keeps
    the split instead of being refused or writing it away."""
    with app_state._lock:
        live = app_state.project.value
        current = app_state.alerts_gdf.value
        if live is None or live.name != target.project or live.active_set != target.alert_set.id:
            return
        if current is None or not set_store.alerts_belong_to(current, target.alert_set):
            return
        app_state.alerts_gdf.set(gdf)


def apply_split(app_state, app_tile_model, evaluators: list[str], include_reviewed: bool) -> EvaluatorSplit:
    """Deal the active set's alerts between ``evaluators`` (spec §4.1) and
    record the split; a re-split replaces the previous one (new ``split_id``,
    no returns). Blocking (parquet): call from a worker thread. Raises
    ``SplitError``, writing nothing: no active set, a review copy, an
    ``evaluator`` attribute layer, no eligible alert, the store rewritten
    meanwhile, or the set being re-run."""
    names = normalize_names(evaluators)
    p, target = _target(app_state, app_tile_model)
    g = grouping.effective(p, target.alert_set)

    def _build(gdf):
        statuses = gdf["status"].tolist() if "status" in gdf.columns else [None] * len(gdf)
        units = None
        if g is not None:
            eligible = [i for i, st in enumerate(statuses) if include_reviewed or not is_reviewed(st)]
            units = review_groups.neighbourhoods(gdf, g.radius_m, area_crs(p), eligible=eligible)
        dealt = deal(statuses, names, include_reviewed, units=units)
        if not any(dealt):
            raise SplitError("evaluators.err.none_eligible")
        staged = gdf.copy()
        staged[EVALUATOR_COLUMN] = dealt
        return staged

    staged = _write(app_state, p, target, _build)
    split = EvaluatorSplit(split_id=uuid.uuid4().hex, evaluators=names, include_reviewed=bool(include_reviewed),
                           created_at=datetime.now(), group_radius_m=g.radius_m if g else None,
                           group_include_reviewed=g.include_reviewed if g else False)
    app_tile_model._update_set(staged, "split", split, target)
    return split


def area_crs(p):
    """The grid CRS the review measures distances and areas in (``None``
    before there is a grid). Same rule as ``state.area_crs`` in the GUI."""
    return p.grid.epsg if p is not None and p.grid is not None else None


def largest_unit(app_state, n_evaluators: int) -> int:
    """``oversized`` for the active set under its effective grouping, from the
    store on hand: the neighbourhoods of the rows the split dealt. 0 when
    grouping is off, there is no split or no alerts. Blocking (the
    neighbourhoods): call from a worker thread."""
    p = app_state.project.value
    s = active_set(p)
    g = grouping.effective(p, s)
    gdf = app_state.alerts_gdf.value
    if g is None or s is None or gdf is None or s.split is None or EVALUATOR_COLUMN not in gdf.columns:
        return 0
    eligible = [i for i, name in enumerate(assignments(gdf).tolist()) if name]
    if not eligible:
        return 0
    return oversized(review_groups.neighbourhoods(gdf, g.radius_m, area_crs(p), eligible=eligible), n_evaluators)


def remove_split(app_state, app_tile_model) -> None:
    """Clear the ``evaluator`` column (all ``""``) and the set's split record
    (spec §4.1). Also what clears assignments whose record was lost
    (``orphan_assignments``). Blocking; raises ``SplitError`` as
    ``apply_split``."""
    p, target = _target(app_state, app_tile_model)

    def _build(gdf):
        if EVALUATOR_COLUMN not in gdf.columns:
            return None  # nothing assigned on disk: only the record goes
        staged = gdf.copy()
        staged[EVALUATOR_COLUMN] = [""] * len(staged)
        return staged

    staged = _write(app_state, p, target, _build)
    if target.alert_set.split is None:
        return
    if staged is None:
        # The store has no column to clear: the record is published against
        # the alerts on hand (``_update_set`` checks they are this set's).
        staged = app_state.alerts_gdf.value
    app_tile_model._update_set(staged, "split", None, target)


def orphan_assignments(p, gdf) -> bool:
    """True when the active set's store (``gdf``) holds evaluator names but
    the set has no split record: the manifest save after a split failed
    (spec §4.4). The step offers Remove split. An ``evaluator`` attribute
    layer is not an orphan split (``apply_split`` refuses it instead)."""
    s = active_set(p)
    if s is None or s.split is not None or gdf is None or _attribute_clash(s):
        return False
    return bool((assignments(gdf) != "").any())
