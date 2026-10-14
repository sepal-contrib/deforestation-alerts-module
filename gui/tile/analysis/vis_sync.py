"""Visualization layers on the Analysis views (spec §10): the layers with
``show_in_analysis``, added switched off, kept in step with the project.
No legend there (D20).

The project subscription runs in the publishing thread (a worker's publish
included, under ``AppState._lock``): it only computes the plan and hands the
removals and the blocking builds to a ``spawn_in_context`` thread.

Each layer is one spec on every view (group ``"vis"``, id ``vis:<name>``), so
the per-alert view's ``PaneLayers.clear("alert")`` never touches it.
"""

import json
import logging
import threading
from datetime import date

from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier
from gui.scripts.solara_threads import spawn_in_context
from gui.scripts.vis_layers import builder, nicfi_month, nicfi_region

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

KEY_PREFIX = "vis:"


def _signature(layer, project) -> str:
    """The layer's definition, plus what its build resolves from the project:
    a NICFI layer draws the AOI's region and, without a month of its own, the
    active period's month, so a switch of either rebuilds it."""
    definition = layer.model_dump(mode="json")
    if layer.kind == "nicfi":
        definition["month"] = nicfi_month(layer, project, date.today())
        definition["region"] = nicfi_region(project.aoi.bounds)
    return json.dumps(definition, sort_keys=True)


def wanted(project) -> dict:
    """``vis:<name>`` → the signature of each layer shown in Analysis (none
    without an AOI: a NICFI layer draws the AOI's region)."""
    if project is None or project.aoi is None:
        return {}
    return {f"{KEY_PREFIX}{name}": _signature(layer, project)
            for name, layer in project.vis_layers.items() if layer.show_in_analysis}


def plan(current: dict, want: dict) -> tuple:
    """The keys to (re)build and the keys to remove, to go from ``current`` to ``want``."""
    build = sorted(k for k, sig in want.items() if current.get(k) != sig)
    remove = sorted(k for k in current if k not in want)
    return build, remove


def wire(widgets, app_state, spawn=spawn_in_context):
    """Keep the Analysis views in step with the project; returns the unsubscribe."""
    # What the views carry (or are about to), key → signature. A sync updates
    # it at once; a run acts on a key only while it still holds the run's view.
    current, lock = {}, threading.Lock()
    # The keys whose build failed, key → signature: toasted once, and not
    # retried until the layer changes or the project is opened again (every
    # alert step saves the project, so a retry per sync would toast per step).
    failed = {}
    # One run at a time: two quick edits of a layer spawn two runs, which must
    # not build the same key at once (they queue here).
    run_lock = threading.Lock()
    # One target: a layer is one spec on every view (``PaneTarget`` on "all").
    maps = (widgets.panes.target("all", group="vis"),)

    def _sync(_value=None):
        project = app_state.project.value
        want = wanted(project)
        with lock:
            build, remove = plan(current, want)
            for k in remove:
                current.pop(k, None)
                failed.pop(k, None)
            for k in build:
                current[k] = want[k]
        if not build and not remove:
            return
        jobs = [(project.vis_layers[k[len(KEY_PREFIX):]], k) for k in build]
        bounds = project.aoi.bounds if project is not None and project.aoi is not None else None

        def _run():
            with run_lock:
                for k in remove:
                    with lock:
                        if k in current:
                            continue  # wanted again by a later sync: its own run rebuilds it
                    for m in maps:
                        m.remove_layer(k, none_ok=True)
                for layer, key in jobs:
                    with lock:
                        if current.get(key) != want[key]:
                            continue  # superseded or removed by a later sync: its own run handles it
                    error = _build(layer, key, project, bounds)
                    if error is None:
                        continue
                    for m in maps:  # no half-built layer stays
                        m.remove_layer(key, none_ok=True)
                    with lock:
                        toast = current.get(key) == want[key] and failed.get(key) != want[key]
                        if toast:
                            failed[key] = want[key]
                    if toast:
                        current_notifier().error(t("steps.layers.err_analysis", name=layer.name, error=error))

        spawn(_run)

    def _build(layer, key, project, bounds):
        """Build ``layer`` on the views; None, or the error text."""
        for m in maps:
            try:
                m.remove_layer(key, none_ok=True)
                builder(m, layer, key, project=project, aoi_bounds=bounds, shown=False)()
            except (PermissionError, FileNotFoundError) as exc:
                logger.warning("Analysis: visualization layer %s not added: %s", key, exc)
                return error_text(exc)
            except Exception as exc:  # noqa: BLE001 - one layer must not stop the others
                logger.exception("Analysis: visualization layer %s failed", key)
                return error_text(exc)
        return None

    def _retry_failed(_value=None):
        # A project opened (again): its failed layers get one more build.
        with lock:
            for k in failed:
                current.pop(k, None)
            failed.clear()
        _sync()

    unsubscribers = [app_state.project.subscribe(_sync),
                     app_state.project_loaded_signal.subscribe(_retry_failed)]
    _sync()

    def unsubscribe():
        for off in unsubscribers:
            off()

    return unsubscribe
