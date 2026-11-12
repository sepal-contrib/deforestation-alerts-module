"""The one way a step changes a project's setup (spec §6.3).

Every protected action boundary (an AOI pick, a source, mask, layer, period,
question or template change, a grid Save) writes through ``update_setup``
instead of ``AppState.update_project``: the review-copy check runs inside the
state lock, on the live project, so nothing is published for a review copy,
and the user reads the banner's text as a warning. ``refuse_setup`` is the
early check a button handler makes before it opens a dialog or starts a job.
"""

from deforestation_alerts.project.review_copy import ReadOnlyProject, read_only, refuse_if_read_only
from gui.i18n import t
from gui.scripts.notify_bridge import current_notifier


def banner_text(p) -> str:
    """The review-copy banner (and refusal) text of ``p``."""
    rc = p.review_copy
    if rc is None:
        return t("migrated.read_only")
    return t("review_copy.read_only", evaluator=rc.evaluator, origin_project=rc.origin_project)


def _locked(p, migrated_ok: bool) -> bool:
    """``read_only``, except a converted v1 project when ``migrated_ok`` (its
    additional layers stay editable: they are display only)."""
    return read_only(p) and not (migrated_ok and p.review_copy is None)


def refuse_setup(app_state, notifier=None, migrated_ok=False) -> bool:
    """True, after a warning, when the open project is a review copy."""
    p = app_state.project.value
    if not _locked(p, migrated_ok):
        return False
    (notifier or current_notifier()).warning(banner_text(p))
    return True


def update_setup(app_state, name: str, fn, notifier=None, migrated_ok=False) -> bool:
    """``app_state.update_project(name, fn)`` for a setup change: refused, with
    nothing published and a warning, when the live project is a review copy.
    Returns what ``update_project`` returns, False when refused. Every other
    exception ``fn`` raises propagates as before."""
    refused = []

    def _guarded(live):
        if _locked(live, migrated_ok):
            # read under the lock: the project may be closed once it is released
            refused.append(banner_text(live))
            refuse_if_read_only(live)  # raised inside the lock: nothing is published
        fn(live)

    try:
        return app_state.update_project(name, _guarded)
    except ReadOnlyProject:
        if not refused:
            raise  # ``fn``'s own refusal, not this gate's
        (notifier or current_notifier()).warning(refused[0])
        return False
