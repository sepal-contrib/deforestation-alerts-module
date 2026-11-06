"""The prepare job state the Alerts and Harmonize steps share (``PrepareJobs``)."""

from gui.scripts.prepare_scope import PrepareJobs, inflight_key


def test_two_steps_share_one_claim():
    jobs = PrepareJobs()
    assert jobs.inflight.claim(inflight_key("p", "2026-10", "GLAD-L"))
    assert inflight_key("p", "2026-10", "GLAD-L") in jobs.inflight  # what a second mounted step reads
