"""The reviewer's SEPAL user name, looked up once per session.

A SEPAL sandbox runs as the generic ``sepal-user`` (``whoami``, ``$USER``,
``getpass``), so the real name comes from the app-launcher session's
``sepal-user`` header or from SEPAL's ``/api/user/current``. Review Saves read
``app_state.reviewer``; they never look it up themselves.
"""

import getpass
import logging
import os
import threading
from typing import Mapping, Optional

from pysepal.solara import get_current_sepal_client, get_current_session_info

logger = logging.getLogger(__name__)


def lookup_name(
    session_username: Optional[str], client, environ: Mapping[str, str] = os.environ
) -> str:
    """Return the reviewer's user name, or "" when it can't be known."""
    try:
        if session_username:
            return session_username
        if client is not None:
            # client.base_url is the bare https://<SEPAL_HOST>; the path carries /api
            return client.get("/api/user/current").json()["username"] or ""
        if "SEPAL" in environ:
            return ""  # getpass would give the sandbox's generic "sepal-user"
        return environ.get("LOCAL_SEPAL_USER") or getpass.getuser()
    except Exception:
        logger.warning("Could not look up the reviewer's user name", exc_info=True)
        return ""


def start_lookup(app_state) -> None:
    """Set ``app_state.reviewer`` from a daemon thread.

    The session readers use the calling thread's context, so they run here;
    only the HTTP call goes to the thread.
    """
    try:
        session_username = get_current_session_info().username
    except Exception:
        session_username = None
    try:
        client = get_current_sepal_client()
    except Exception:
        client = None

    def _run():
        app_state.reviewer = lookup_name(session_username, client)

    threading.Thread(target=_run, name="reviewer-lookup", daemon=True).start()
