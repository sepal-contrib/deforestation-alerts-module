"""Merging a reviewed copy into the creator's active set (spec §7.4).
Solara-free: the Evaluators step calls ``merge_return`` from a worker thread.

The merge follows the store mutation path of a split (``split_service``) and a
review Save: under the store's lock (``alert_store.store_lock``) it reads the
store stamped with its generation, classifies the return against it again (a
Save may have landed since the preview), builds the merged frame and writes
it with a guard that refuses when the generation moved or an Integrate job of
the set is running. Then the live alerts are republished (the next Save must
carry the merged rows), ``n_reviewed`` and ``split.returns`` are published
through ``SetContext._update_set`` (with ``n_groups``), and an alert open in Analysis whose row
was written is reloaded from the store (``AnalysisState.reload_alert``).
"""

from datetime import datetime
from typing import Literal, Optional

from deforestation_alerts.project.models import EvaluatorReturn
from deforestation_alerts.project.review_groups import n_groups
from deforestation_alerts.project.review_status import reviewed_mask
from deforestation_alerts.project.share import ReturnPreview, ShareError, apply_return, classify_return
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet, store_lock
from gui.i18n import t
from gui.scripts import set_store
from gui.scripts.integrate_jobs import is_integrating
from gui.store.project_writers import writing


def _written(preview: ReturnPreview, choice: str) -> list[int]:
    return list(preview.new) + (list(preview.conflicts) if choice == "theirs" else [])


def _target(app_state, app_tile_model, preview: ReturnPreview):
    """The open project and the active set's pinned target, while they are
    still what the preview was read for; else ``ShareError``."""
    p = app_state.project.value
    if p is not None and p.review_copy is not None:
        raise ShareError("share.merge_err_review_copy")
    target = app_tile_model.pin()
    if p is None or target is None:
        raise ShareError("share.merge_err_changed")
    s, h = target.alert_set, preview.header
    if (s.id != h.set_id or s.fingerprint != h.set_fingerprint or s.split is None
            or s.split.split_id != h.split_id or h.evaluator not in s.split.evaluators):
        raise ShareError("share.merge_err_changed")
    if is_integrating(target.project, s.id):
        raise ShareError("evaluators.err.integrating")
    return p, target


def _republish(app_state, target, gdf) -> bool:
    """Hand the merged alerts to Analysis while the set is still the open
    project's active set and the alerts on hand are its (as
    ``split_service._republish``). True when they were handed over."""
    with app_state._lock:
        live = app_state.project.value
        current = app_state.alerts_gdf.value
        if live is None or live.name != target.project or live.active_set != target.alert_set.id:
            return False
        if current is None or not set_store.alerts_belong_to(current, target.alert_set):
            return False
        app_state.alerts_gdf.set(gdf)
        return True


def _reload_selected(app_state, analysis, gdf, written: list[int]) -> None:
    """Spec §7.4: the alert selected in Analysis is seeded again from the
    merged row when that row was written (its unsaved edits are dropped)."""
    pos = app_state.actual_alert_id.value
    if analysis is None or pos is None or not 0 <= pos < len(gdf):
        return
    if int(gdf["alert_id"].iloc[pos]) in set(written):
        analysis.reload_alert.set(analysis.reload_alert.value + 1)


def merge_return(app_state, app_tile_model, analysis, preview: ReturnPreview,
                 choice: Literal["mine", "theirs"]) -> EvaluatorReturn:
    """Merge the reviewed copy ``preview`` (``share.read_return``) into the
    active set's store and record it on the set's split (spec §7.4).
    Blocking (parquet): call from a worker thread. Raises ``ShareError``,
    writing nothing: a review copy, the project or set changed since the
    preview, the store rewritten during the merge, the set being re-run."""
    p, target = _target(app_state, app_tile_model, preview)
    store = set_store.store_path(p, target.alert_set)
    with writing(target.project), store_lock(store):
        gdf = set_store.load_set_alerts(p, target.alert_set)  # stamped with its generation
        if gdf is None:
            raise ShareError("share.merge_err_no_alerts")
        fresh = classify_return(preview, gdf)  # the store as it is now
        written = _written(fresh, choice)
        merged = apply_return(gdf, fresh, choice)
        if written:

            def _guard():
                if set_store.generation_changed(gdf, store):
                    raise ShareError("share.merge_err_store_changed")
                if is_integrating(target.project, target.alert_set.id):
                    raise ShareError("evaluators.err.integrating")

            save_gdf_to_parquet(merged, store, guard=_guard)
            set_store.stamp_generation(merged, store)
    record = EvaluatorReturn(merged_at=datetime.now(), taken=len(written), unchanged=len(fresh.unchanged),
                             conflicts=len(fresh.conflicts), choice=choice if fresh.conflicts else None,
                             ignored=len(fresh.ignored))
    republished = written and _republish(app_state, target, merged)
    app_tile_model._update_set(merged, "n_reviewed", int(reviewed_mask(merged).sum()), target)
    app_tile_model._update_set(merged, "n_groups", n_groups(merged), target)
    live = app_state.project.value
    s = live.alert_sets.get(target.alert_set.id) if live is not None and live.name == target.project else None
    if s is not None and s.split is not None and s.split.split_id == fresh.header.split_id:
        returns = {**s.split.returns, fresh.header.evaluator: record}
        app_tile_model._update_set(merged, "split", s.split.model_copy(update={"returns": returns}), target)
    if republished:
        _reload_selected(app_state, analysis, merged, written)
    return record


def selected_alert_written(app_state, preview: ReturnPreview, choice: str) -> Optional[int]:
    """The 1-based number of the alert open in Analysis when a merge with
    ``choice`` would write its row (the preview names it, spec §7.4), else None."""
    gdf, pos = app_state.alerts_gdf.value, app_state.actual_alert_id.value
    if gdf is None or "alert_id" not in gdf.columns or pos is None or not 0 <= pos < len(gdf):
        return None
    return pos + 1 if int(gdf["alert_id"].iloc[pos]) in set(_written(preview, choice)) else None


def preview_lines(preview: ReturnPreview, selected: Optional[int] = None) -> list[str]:
    """The preview dialog's text (spec §7.3, §7.1, §7.4): the counts, then
    the questionnaire warning, the ignored columns and the alert to reload.
    When groups are involved (``preview.n_groups``), a count whose rows touch
    groups reads "N alerts (M groups)" (spec 2026-10-26 §8.4)."""

    def count(name: str):
        n = len(getattr(preview, name))
        groups = preview.class_groups.get(name, 0) if preview.n_groups > 0 else 0
        return t("share.merge_summary_groups", count=groups, n=n, groups=groups) if groups else n

    lines = [t("share.merge_summary", evaluator=preview.header.evaluator, new=count("new"),
               conflicts=count("conflicts"), unchanged=count("unchanged"), ignored=len(preview.ignored))]
    if preview.questions_changed:
        lines.append(t("share.merge_questions_changed"))
    if preview.extra_columns:
        lines.append(t("share.merge_extra_columns", columns=", ".join(preview.extra_columns)))
    if selected is not None:
        lines.append(t("share.merge_selected_reload", n=selected))
    return lines
