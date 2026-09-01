"""Kernel-scoped registry of the ``Workspace`` objects whose container-local
scratch must be swept when a kernel goes away.

WHY IT EXISTS
-------------
``Workspace`` stages full-size artifacts on the container's disk (the analyze
GeoTIFF, the alert store it downloads before reading, the CNN before/after
composite plus its prediction raster) and ``Workspace.cleanup_scratch()`` is the
bulk sweep for them. Nothing was calling it: the ``Workspace`` is built per-Page
inside a ``use_memo``, while the only teardown seam the app has —
``@solara.lab.on_kernel_start``'s returned cleanup callable — is module level, so
the two could not see each other. This registry is that seam: ``Page()``
registers the Workspace it just built, the kernel-start hook's cleanup sweeps
everything the *calling kernel* registered.

WHY KERNEL-SCOPED (and not one process-wide list)
-------------------------------------------------
One container process serves several virtual kernels (browser tabs, and on the
app-launcher target several sessions). A process-wide sweep on any one teardown
would delete the scratch a *live* kernel's analyze run is still reading. Both
sides run inside the kernel context — solara's ``VirtualKernelContext.close()``
invokes the on-close callbacks under ``with self`` — so
``solara.get_kernel_id()`` resolves the same key for registration and for the
sweep.

Registration holds only weak references: a registry entry must never be the
thing that keeps a Workspace (and through it the session client) alive.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Any

import solara

logger = logging.getLogger("SEPALUI.deforestation_alerts")

# Bucket used when there is no virtual kernel at all (tests, a plain import, the
# voila/IPython fallback failing). There is only ever one "session" in that case,
# so a single shared bucket is the right answer rather than an error.
_NO_KERNEL = "<no-kernel>"

_LOCK = threading.Lock()
_OWNERS: dict[str, "weakref.WeakSet[Any]"] = {}


def _kernel_key() -> str:
    """The current kernel's id, or ``_NO_KERNEL`` outside a kernel."""
    try:
        return str(solara.get_kernel_id())
    except Exception:  # RuntimeError outside a kernel; be defensive about the rest
        return _NO_KERNEL


def register_scratch_owner(workspace: Any) -> None:
    """Remember ``workspace`` so this kernel's teardown sweeps its scratch."""
    with _LOCK:
        _OWNERS.setdefault(_kernel_key(), weakref.WeakSet()).add(workspace)


def cleanup_kernel_scratch() -> int:
    """Sweep the scratch of every Workspace registered by the calling kernel.

    Returns the number of workspaces swept. Never raises: it runs on a teardown
    path where an exception would abort the rest of the kernel's cleanup.
    """
    with _LOCK:
        owners = _OWNERS.pop(_kernel_key(), None)
        workspaces = list(owners) if owners is not None else []
    swept = 0
    for workspace in workspaces:
        try:
            workspace.cleanup_scratch()
            swept += 1
        except Exception:
            logger.exception("Scratch sweep failed for %r", workspace)
    if swept:
        logger.info("Swept container-local scratch for %s workspace(s)", swept)
    return swept
