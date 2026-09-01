"""One event loop for all eeclient traffic: the app uses the blocking GEE API
inside asyncio.to_thread exclusively (Decision 3). A stray *_async call would
bind eeclient to a second loop and crash far from the call site."""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_no_async_gee_transport_mixed_in():
    result = subprocess.run(
        ["grep", "-rn", "_async(", str(REPO / "gui"),
         str(REPO / "deforestation_alerts"), "--include=*.py"],
        capture_output=True, text=True,
    )
    # grep's exit code doubles as "did the search actually happen": 0 = matches
    # found, 1 = none found — both mean grep ran to completion over real paths.
    # 2 means a usage/path error (e.g. gui/ or deforestation_alerts/ renamed out
    # from under this test), which would leave stdout empty too — asserting on
    # stdout alone would then pass while checking nothing, silently advertising
    # protection this test isn't providing. Fail loudly on 2 instead.
    assert result.returncode in (0, 1), (
        f"grep did not run as expected (exit {result.returncode}): {result.stderr}"
    )
    assert result.stdout == "", (
        "Async GEE calls found — the app is sync-via-thread only:\n" + result.stdout
    )
