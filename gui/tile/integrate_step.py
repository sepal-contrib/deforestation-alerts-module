"""Integrate & vectorize step (spec §7.4).

The step is mounted for the whole session, so ``IntegrateStep`` is a gate
over the project reactive and the form is a child keyed by the project load,
the project and the active period (GridForm's pattern, ruling #18): opening,
reopening or switching a project remounts it with that project's values, so
one project's sources, sizes or attribute layers never run in
another. The child gets plain values, never the project, and reads the live
project only when Run is clicked.

The gate owns what must outlive those remounts: the in-flight keys, one per
project (``integrate:{project}``). A run still finishing for a project the
user left neither shows as running in, nor blocks, the project open now,
while reopening its own project still finds it running. They are claimed on
a click and released by the worker, never from a render or an effect.

The source checklist (spec §7.4) offers every source whose raw and harmonized
files are current, with what its harmonized file found, and shows a source of
the period that is not covered or has no data disabled, with the reason; the
run is told which were left out, for an empty set's toast.
"""

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED
from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.engine.integrate import IntegrationJob, min_cluster_pixels
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.engine.presets import has_probable
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import set_id as make_set_id
from gui.i18n import t
from gui.scripts import alert_layers
from gui.scripts.coverage_ui import note_text, result_note
from gui.scripts.inflight import InflightKeys
from gui.scripts.integrate_jobs import (
    params_from_form,
    replace_message,
    reviewed_in,
    reviewed_lines,
    run_integration_job,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store import workflow_steps
from gui.widget.attribute_form import layer_label
from gui.widget.attribute_modal import AttributeModal
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable
from gui.widget.text_style import MUTED

SORTINGS = [1, 2, 3, 4]


def _inflight_key(project: str) -> str:
    return f"integrate:{project}"


def integrate_choices(p, pdir, period) -> list:
    """The checklist: ``{"source", "label", "enabled", "note"}`` for every
    ready source (enabled, what its harmonized file found as the note) and
    every source not covered or without data for the period (disabled, the
    reason as the note). A source merely not built yet is not listed."""
    out = []
    for r in plan_prepare(p, pdir, period):
        if r.ready:
            note = result_note(r.stats)
            out.append({"source": r.source, "label": r.source, "enabled": True, "note": note["text"] if note else "",
                        "probable": has_probable(p.sources[r.source])})
        elif r.coverage in (NOT_COVERED, NO_DATA):
            out.append({"source": r.source, "label": r.source, "enabled": False,
                        "note": note_text(r.notes[0]) if r.notes else ""})
    return out


#: Edit and delete: the attribute table's actions (its budget, tests/test_table_budget.py).
ATTRIBUTE_ACTIONS = 2


def attribute_columns() -> list:
    """The attribute-layer table's columns: the raster (flex) and the actions."""
    return [{"label": t("steps.integrate.attr_col_raster")}]


def attribute_rows(layers, *, edit, delete) -> list:
    """The ``ProductTable`` rows of the attribute layers (spec D3); pure.
    ``edit`` and ``delete`` take the layer's index."""
    return [{
        "key": f"{i}:{layer.get('source')}",
        "cells": [{"value": layer_label(layer)}],
        "notes": [{"text": t("steps.integrate.attr_columns",
                             columns=", ".join(b["column"] for b in layer.get("bands", []))), "tone": None}],
        "actions": [{"kind": "edit", "on_click": lambda i=i: edit(i)},
                    {"kind": "delete", "on_click": lambda i=i: delete(i)}],
    } for i, layer in enumerate(layers)]


@solara.component
def IntegrateStep(app_state, map_, gee_interface, layers):
    # Session-wide, like the step: a run outlives the form it was started from.
    inflight = solara.use_memo(InflightKeys, [])
    p = app_state.project.value
    period = workflow_steps.active_period(p)
    choices = integrate_choices(p, paths.project_dir(p.name), period) if period is not None and p.grid else []
    ready = [c["source"] for c in choices if c["enabled"]]
    if not ready:
        solara.Info(t("workflow.lock_integrate"))
        return
    active = workflow_steps.active_set(p)
    # The active set's status (spec §5.3, review focus 4): "stale" or
    # "missing" shows a line that says to run again; a current set shows none.
    status = set_status(p, paths.project_dir(p.name), active) if active is not None else None
    _IntegrateForm(
        app_state=app_state, gee_interface=gee_interface, inflight=inflight, layers=layers,
        name=p.name, period=period, choices=choices, resolution=p.grid.resolution,
        last=active.params if active is not None else None,
        has_set=active is not None, has_states=alert_layers.has_states(p, active),
        has_earlier=bool(alert_layers.earlier_of(p, active)),
        active_label=f"{active.period_id} · {' + '.join(active.sources)}" if active is not None else None,
        active_status=status.value if status in (Status.STALE, Status.MISSING) else None,
    ).key(f"integrate-{app_state.project_loaded_signal.value}-{p.name}-{period.id}")


@solara.component
def _IntegrateForm(app_state, gee_interface, inflight, layers, name, period, choices, resolution, last,
                   has_set=False, has_states=False, has_earlier=False, active_label=None, active_status=None):
    notifications = use_notifications()
    ready = [c["source"] for c in choices if c["enabled"]]
    left_out = {c["source"]: c["note"] for c in choices if not c["enabled"]}
    picked, set_picked = solara.use_state(None)  # None: every ready source
    min_size, set_min_size = solara.use_state(str(last.min_size_ha if last else 0.5))
    probable_off, set_probable_off = solara.use_state(set(last.confirmed_only) if last else set())
    sorting, set_sorting = solara.use_state(last.sorting if last else 1)
    max_alerts, set_max_alerts = solara.use_state(str(last.max_alerts if last else 0))
    # The attribute layers (spec D3) are form state until Run, like the fields
    # above. A set made before the list reopens with its one raster.
    attr_layers, set_attr_layers = solara.use_state(last.attribute_list() if last else [])
    attr_modal = solara.use_reactive(False)  # the modal mounts its form (an asset listing) only when open
    attr_editing, set_attr_editing = solara.use_state(None)  # the layer's index; None: adding
    confirm, set_confirm = solara.use_state(None)  # (params, message, reviewed lines) awaiting Replace

    key = _inflight_key(name)
    busy = key in inflight.value
    chosen = [s for s in (picked if picked is not None else ready) if s in ready]
    sid = make_set_id(period.id, chosen) if chosen else None
    try:
        min_px = min_cluster_pixels(float(min_size), resolution)
    except (TypeError, ValueError, OverflowError):
        min_px = "?"

    def _toggle_source(source, on):
        current = set(chosen)
        (current.add if on else current.discard)(source)
        set_picked([s for s in ready if s in current])

    def _launch(params):
        live = app_state.project.value
        if live is None or live.name != name:
            return  # closed or switched since this form rendered
        if not inflight.claim(key):
            return
        job = IntegrationJob(project=live.model_copy(deep=True), pdir=paths.project_dir(name), period=period,
                             sources=list(chosen), params=params)

        def _run():
            try:
                run_integration_job(app_state, notifications, job, left_out=left_out)
            finally:
                inflight.release(key)

        spawn_in_context(_run)

    def _on_run():
        params, err = params_from_form(min_size, sorting, max_alerts, attr_layers,
                                       confirmed_only=sorted(x for x in probable_off if x in chosen))
        if err:
            notifications.error(t(err))
            return
        live = app_state.project.value
        message = replace_message(live, sid) if live is not None and live.name == name else None
        if message:
            # The lines read the set's store: only when there are reviews to list.
            lines = reviewed_lines(live, sid) if reviewed_in(live, sid) else []
            set_confirm((params, message, lines))
            return
        _launch(params)

    def _confirm():
        params, _, _ = confirm
        set_confirm(None)
        _launch(params)

    def _open_attr(index=None):
        set_attr_editing(index)
        attr_modal.set(True)

    def _save_attr(index, layer):
        set_attr_layers(lambda cur: [*cur, layer] if index is None
                        else [layer if j == index else old for j, old in enumerate(cur)])

    def _delete_attr(index):
        set_attr_layers(lambda cur: [old for j, old in enumerate(cur) if j != index])

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.integrate.title')}")
        StepIntro(t("steps.integrate.description"), t("steps.integrate.info_header"), t("steps.integrate.info_md"))
        solara.Text(t("steps.integrate.sources"), style="font-weight:600;")
        for c in choices:  # a disabled one shows why it is left out
            s = c["source"]
            rv.Checkbox(label=f"{c['label']} · {c['note']}" if c["note"] else c["label"], v_model=s in chosen,
                        disabled=not c["enabled"], dense=True, hide_details=True,
                        on_v_model=lambda v, s=s: _toggle_source(s, v))
            if s in chosen and c["probable"]:
                rv.Switch(label=t("steps.integrate.include_probable", source=c["label"]), v_model=s not in probable_off,
                          on_v_model=lambda v, s=s: set_probable_off(lambda cur: (cur - {s}) if v else (cur | {s})),
                          dense=True, hide_details=True, class_="ml-6 mt-0")
        rv.TextField(label=t("steps.integrate.min_size"), v_model=min_size, on_v_model=set_min_size, type="number",
                     suffix="ha", dense=True, outlined=True, hint=t("steps.integrate.min_size_hint", px=min_px),
                     persistent_hint=True)
        rv.Select(label=t("steps.integrate.sorting"), v_model=sorting, on_v_model=set_sorting, dense=True,
                  outlined=True, items=[{"text": t(f"steps.integrate.sorting_{k}"), "value": k} for k in SORTINGS])
        rv.TextField(label=t("steps.integrate.max_alerts"), v_model=max_alerts, on_v_model=set_max_alerts,
                     type="number", dense=True, outlined=True, hint=t("steps.integrate.max_alerts_hint"),
                     persistent_hint=True)
        # A nested list: an outlined card (422 px of content) with an outlined Add above the table (spec D4).
        with rv.Card(outlined=True, class_="pa-3"):
            solara.Button(t("steps.integrate.attr_add"), icon_name="mdi-plus", small=True, outlined=True,
                          block=True, classes=["mb-2"], on_click=lambda: _open_attr(None))
            ProductTable(title=t("steps.integrate.attr_list_title"), columns=attribute_columns(),
                         rows=attribute_rows(attr_layers, edit=_open_attr, delete=_delete_attr),
                         empty_text=t("steps.integrate.attr_empty"))
        if active_status is not None:
            solara.Text(t(f"steps.integrate.set_{active_status}", set=active_label), classes=["warning--text"])
        if sid:
            solara.Text(t("steps.integrate.will_create", period=period.id, sources=" + ".join(chosen)),
                        style=MUTED)
        solara.Button(t("steps.integrate.run"), color="primary", small=True, icon_name="mdi-vector-union",
                      disabled=busy or not chosen, loading=busy, on_click=_on_run)
        if has_set:
            # The alerts of the run that just finished, on the shared map. Two
            # booleans: the shell's _AlertLayers leaf owns the MapLayers calls,
            # because a layer switched on here stays on while the user moves to
            # Analysis or opens the Overview dashboard, and this form unmounts
            # with the step. A switch is held while its own layer is being
            # built, so a flip can never race the build it started.
            inflight_keys = layers.inflight.value
            rv.Switch(label=t("steps.integrate.show_alerts"), v_model=app_state.show_alerts.value,
                      on_v_model=app_state.show_alerts.set, dense=True, hide_details=True,
                      disabled=alert_layers.SET_LAYER_KEY in inflight_keys)
            if has_states:
                rv.Switch(label=t("steps.integrate.show_unanalysable"), v_model=app_state.show_unanalysable.value,
                          on_v_model=app_state.show_unanalysable.set, dense=True, hide_details=True,
                          disabled=alert_layers.STATES_LAYER_KEY in inflight_keys)
            if has_earlier:
                rv.Switch(label=t("steps.integrate.show_earlier"), v_model=app_state.show_earlier_sets.value,
                          on_v_model=app_state.show_earlier_sets.set, dense=True, hide_details=True,
                          disabled=any(k.startswith(alert_layers.EARLIER_PREFIX) for k in inflight_keys))

    ConfirmDialog(
        open=confirm is not None,
        on_cancel=lambda: set_confirm(None),
        on_confirm=_confirm,
        title=t("steps.integrate.replace_title"),
        message=confirm[1] if confirm else None,
        confirm_label=t("common.replace"),
        confirm_color="warning",
        details=confirm[2] if confirm else None,
    )
    AttributeModal(open_=attr_modal, gee_interface=gee_interface, layers=attr_layers, editing=attr_editing,
                   on_save=_save_attr)
