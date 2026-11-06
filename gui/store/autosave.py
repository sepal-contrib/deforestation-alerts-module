"""Save the project after every committed change (spec D11).

The hook subscribes to ``app_state.project`` and saves synchronously, in the
thread that published: ``commit``, ``update_project`` and the
``*_project_state`` methods publish under ``AppState._lock``, so saves never
overlap and a job's result is on disk before ``update_project`` returns. A
load is skipped (it would only rewrite the file just read), and so is a close.
A failed save lands in ``save_error``; it is never raised into the publisher
(an event handler, a task or a worker). See ``gui/store/state_manager.py``
for the lock ordering this implies.
"""

import logging
from datetime import datetime

from deforestation_alerts.project import io

logger = logging.getLogger("SEPALUI.deforestation_alerts")


def install_autosave(app_state, save=io.save_project):
    """Start saving ``app_state``'s project on every publish; returns the unsubscribe."""

    def _on_change(p):
        if p is None or app_state._loading:
            return
        try:
            save(p)
        except Exception as exc:  # noqa: BLE001 - reported, never raised into the publisher
            logger.exception("auto-save failed for %s", p.name)
            app_state.save_error.set(str(exc) or exc.__class__.__name__)
            return
        app_state.last_saved.set(datetime.now())
        app_state.save_error.set(None)

    return app_state.project.subscribe(_on_change)
