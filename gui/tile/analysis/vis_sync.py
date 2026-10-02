"""Visualization layers on the two Analysis maps (spec §10): the layers with
``show_in_analysis``, added switched off, kept in step with the project.
No legend there (D20).

The project subscription runs in the publishing thread (a worker's publish
included, under ``AppState._lock``): it only computes the plan and hands the
removals and the blocking builds to a ``spawn_in_context`` thread.
"""

import json
import logging
import threading
from datetime import date

from gui.scripts.solara_threads import spawn_in_context
from gui.scripts.vis_layers import builder, nicfi_month, nicfi_region

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

_NO_BOUNDS = (0, 0, 0, 0)


def _bounds(project):
    return project.aoi.bounds if project is not None and project.aoi is not None else _NO_BOUNDS


def _signature(layer, project) -> str:
    """The layer's definition, plus what its build resolves from the project:
    a NICFI layer draws the AOI's region and, without a month of its own, the
    active period's month, so a switch of either rebuilds it."""
    definition = layer.model_dump(mode="json")
    if layer.kind == "nicfi":
        definition["month"] = nicfi_month(layer, project, date.today())
        definition["region"] = nicfi_region(_bounds(project))
    return json.dumps(definition, sort_keys=True)


def wanted(project) -> dict:
    """``vis:<name>`` → the signature of each layer shown in Analysis."""
    if project is None:
        return {}
    return {f"vis:{name}": _signature(layer, project)
            for name, layer in project.vis_layers.items() if layer.show_in_analysis}


def plan(current: dict, want: dict) -> tuple:
    """The keys to (re)build and the keys to remove, to go from ``current`` to ``want``."""
    build = sorted(k for k, sig in want.items() if current.get(k) != sig)
    remove = sorted(k for k in current if k not in want)
    return build, remove


def wire(widgets, app_state, spawn=spawn_in_context):
    """Keep both Analysis maps in step with the project; returns the unsubscribe."""
    # What the maps carry (or are about to), key → signature. A sync updates
    # it at once; a run acts on a key only while it still holds the run's view.
    current, lock = {}, threading.Lock()
    # One run at a time: two quick edits of a layer spawn two runs, which must
    # not build the same key on the same map at once (they queue here).
    run_lock = threading.Lock()
    maps = (widgets.map_before, widgets.map_after)

    def _sync(_value=None):
        project = app_state.project.value
        want = wanted(project)
        with lock:
            build, remove = plan(current, want)
            for k in remove:
                current.pop(k, None)
            for k in build:
                current[k] = want[k]
        if not build and not remove:
            return
        jobs = [(m, project.vis_layers[k[4:]], k) for k in build for m in maps]
        bounds = _bounds(project)

        def _run():
            with run_lock:
                for k in remove:
                    with lock:
                        if k in current:
                            continue  # wanted again by a later sync: its own run rebuilds it
                    for m in maps:
                        m.remove_layer(k, none_ok=True)
                for m, layer, key in jobs:
                    with lock:
                        if current.get(key) != want[key]:
                            continue  # superseded or removed by a later sync: its own run handles it
                    try:
                        m.remove_layer(key, none_ok=True)
                        builder(m, layer, key, project=project, aoi_bounds=bounds, shown=False)()
                    except (PermissionError, FileNotFoundError) as exc:
                        logger.warning("Analysis: visualization layer %s not added: %s", key, exc)
                    except Exception:  # noqa: BLE001 - one layer must not stop the others
                        logger.exception("Analysis: visualization layer %s failed", key)

        spawn(_run)

    unsubscribe = app_state.project.subscribe(_sync)
    _sync()
    return unsubscribe
