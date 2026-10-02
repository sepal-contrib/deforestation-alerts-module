"""Integrate & vectorize step (spec §7.4).

The step is mounted for the whole session, so ``IntegrateStep`` is a gate
over the project reactive and the form is a child keyed by the project load,
the project and the active period (GridForm's pattern, ruling #18): opening,
reopening or switching a project remounts it with that project's values, so
one project's sources, sizes, drawing or attribute raster never run in
another. The child gets plain values, never the project, and reads the live
project only when Run is clicked.

The gate owns what must outlive those remounts: the in-flight keys, one per
project (``integrate:{project}``). A run still finishing for a project the
user left neither shows as running in, nor blocks, the project open now,
while reopening its own project still finds it running. They are claimed on
a click and released by the worker, never from a render or an effect.

The drawn-area ``DrawControl`` belongs to the form and is on the shared map
only while this step is showing with "Drawn polygon" chosen (ruling #77).
pysepal's ``show()`` and ``hide()`` both clear the drawing, so they run only
when that visibility changes, and the form's unmount takes its control off
the map.

The source checklist (spec §7.4) offers every source whose raw and harmonized
files are current, with what its harmonized file found, and shows a source of
the period that is not covered or has no data disabled, with the reason; the
run is told which were left out, for an empty set's toast.
"""

from pathlib import Path

import reacton.ipyvuetify as rv
import solara
from pysepal.mapping.draw_control import DrawControl
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED
from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.engine.integrate import IntegrationJob, min_cluster_pixels
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import set_id as make_set_id
from gui.i18n import t, tn
from gui.scripts import alert_layers
from gui.scripts.coverage_ui import note_text, result_note
from gui.scripts.inflight import InflightKeys
from gui.scripts.integrate_jobs import (
    params_from_form,
    reviewed_in,
    reviewed_lines,
    run_integration_job,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store import workflow_steps
from gui.widget.attribute_form import AttributeForm
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.help import StepIntro

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
            out.append({"source": r.source, "label": r.source, "enabled": True, "note": note["text"] if note else ""})
        elif r.coverage in (NOT_COVERED, NO_DATA):
            out.append({"source": r.source, "label": r.source, "enabled": False,
                        "note": note_text(r.notes[0]) if r.notes else ""})
    return out


def _short(attribute) -> str:
    """A GeoTIFF by its file name; an Earth Engine asset id (dots included) as is."""
    src = str(attribute["source"])
    return Path(src).name if attribute.get("kind") == "local" else src


def _attribute_title(attribute) -> str:
    if attribute and attribute.get("bands"):
        return t("steps.integrate.attributes_named", name=_short(attribute))
    return t("steps.integrate.attributes")


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
    showing = app_state.active_step.value == workflow_steps.step_index("integrate")
    # The active set's status (spec §5.3, review focus 4): "stale" or
    # "missing" shows a line that says to run again; a current set shows none.
    status = set_status(p, paths.project_dir(p.name), active) if active is not None else None
    _IntegrateForm(
        app_state=app_state, map_=map_, gee_interface=gee_interface, inflight=inflight, layers=layers,
        name=p.name, period=period, choices=choices, resolution=p.grid.resolution,
        last=active.params if active is not None else None, showing=showing,
        has_set=active is not None, has_earlier=bool(alert_layers.earlier_of(p, active)),
        active_label=f"{active.period_id} · {' + '.join(active.sources)}" if active is not None else None,
        active_status=status.value if status in (Status.STALE, Status.MISSING) else None,
    ).key(f"integrate-{app_state.project_loaded_signal.value}-{p.name}-{period.id}")


@solara.component
def _IntegrateForm(app_state, map_, gee_interface, inflight, layers, name, period, choices, resolution, last,
                   showing, has_set=False, has_earlier=False, active_label=None, active_status=None):
    notifications = use_notifications()
    ready = [c["source"] for c in choices if c["enabled"]]
    left_out = {c["source"]: c["note"] for c in choices if not c["enabled"]}
    draw = solara.use_memo(lambda: DrawControl(map_), [])
    draw_shown = solara.use_ref(False)
    picked, set_picked = solara.use_state(None)  # None: every ready source
    min_size, set_min_size = solara.use_state(str(last.min_size_ha if last else 0.5))
    area_mode, set_area_mode = solara.use_state(last.area_mode if last else "aoi")
    sorting, set_sorting = solara.use_state(last.sorting if last else 1)
    max_alerts, set_max_alerts = solara.use_state(str(last.max_alerts if last else 0))
    attribute, set_attribute = solara.use_state(last.attribute_config if last else None)
    # The attribute form lists the user's Earth Engine assets when it mounts:
    # it waits for its panel to be opened once, then stays mounted.
    attribute_opened, set_attribute_opened = solara.use_state(False)
    # The panel's open index (None: closed). Passing it as v_model is what makes
    # ipyvue bind the browser's v-model at all: left at its "!!disabled!!"
    # default, a click opens the panel in the browser but never reaches
    # _on_panel. A constant v_model is not enough either: the re-render that
    # mounts the form would push it back and close the panel.
    attribute_panel, set_attribute_panel = solara.use_state(None)
    confirm, set_confirm = solara.use_state(None)  # (params, reviewed count, their lines) awaiting Replace

    def _sync_draw():
        visible = showing and area_mode == "drawn"
        if visible != draw_shown.current:
            (draw.show if visible else draw.hide)()
            draw_shown.current = visible

    solara.use_effect(_sync_draw, [showing, area_mode])
    solara.use_effect(lambda: draw.hide, [])  # unmount: this form's control leaves the map

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
        drawing = draw.to_json() if area_mode == "drawn" else None
        params, err = params_from_form(min_size, area_mode, drawing, sorting, max_alerts, attribute)
        if err:
            notifications.error(t(err))
            return
        live = app_state.project.value
        reviewed = reviewed_in(live, sid) if live is not None and live.name == name else 0
        if reviewed:
            set_confirm((params, reviewed, reviewed_lines(live, sid)))
            return
        _launch(params)

    def _confirm():
        params, _, _ = confirm
        set_confirm(None)
        _launch(params)

    def _on_panel(open_index):
        set_attribute_panel(open_index)
        if open_index is not None:
            set_attribute_opened(True)

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.integrate.title')}")
        StepIntro(t("steps.integrate.description"), t("steps.integrate.info_header"), t("steps.integrate.info_md"))
        solara.Text(t("steps.integrate.sources"), style="font-weight:600;")
        for c in choices:  # a disabled one shows why it is left out
            s = c["source"]
            rv.Checkbox(label=f"{c['label']} · {c['note']}" if c["note"] else c["label"], v_model=s in chosen,
                        disabled=not c["enabled"], dense=True, hide_details=True,
                        on_v_model=lambda v, s=s: _toggle_source(s, v))
        rv.TextField(label=t("steps.integrate.min_size"), v_model=min_size, on_v_model=set_min_size, type="number",
                     suffix="ha", dense=True, outlined=True, hint=t("steps.integrate.min_size_hint", px=min_px),
                     persistent_hint=True)
        rv.Select(label=t("steps.integrate.area"), v_model=area_mode, on_v_model=set_area_mode, dense=True,
                  outlined=True, items=[{"text": t("steps.integrate.area_aoi"), "value": "aoi"},
                                        {"text": t("steps.integrate.area_drawn"), "value": "drawn"}])
        rv.Select(label=t("steps.integrate.sorting"), v_model=sorting, on_v_model=set_sorting, dense=True,
                  outlined=True, items=[{"text": t(f"steps.integrate.sorting_{k}"), "value": k} for k in SORTINGS])
        rv.TextField(label=t("steps.integrate.max_alerts"), v_model=max_alerts, on_v_model=set_max_alerts,
                     type="number", dense=True, outlined=True, hint=t("steps.integrate.max_alerts_hint"),
                     persistent_hint=True)
        with rv.ExpansionPanels(flat=True, v_model=attribute_panel, on_v_model=_on_panel):
            with rv.ExpansionPanel():
                rv.ExpansionPanelHeader(children=[_attribute_title(attribute)])
                with rv.ExpansionPanelContent():
                    if attribute_opened:
                        AttributeForm(gee_interface=gee_interface, value=attribute, on_value=set_attribute)
        if active_status is not None:
            solara.Text(t(f"steps.integrate.set_{active_status}", set=active_label), classes=["warning--text"])
        if sid:
            solara.Text(t("steps.integrate.will_create", period=period.id, sources=" + ".join(chosen)),
                        classes=["text--secondary"])
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
            if has_earlier:
                rv.Switch(label=t("steps.integrate.show_earlier"), v_model=app_state.show_earlier_sets.value,
                          on_v_model=app_state.show_earlier_sets.set, dense=True, hide_details=True,
                          disabled=any(k.startswith(alert_layers.EARLIER_PREFIX) for k in inflight_keys))

    ConfirmDialog(
        open=confirm is not None,
        on_cancel=lambda: set_confirm(None),
        on_confirm=_confirm,
        title=t("steps.integrate.replace_title"),
        message=tn("steps.integrate.replace_message", confirm[1] if confirm else 0),
        confirm_label=t("common.replace"),
        confirm_color="warning",
        details=confirm[2] if confirm else None,
    )
