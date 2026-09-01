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
    assert result.stdout == "", (
        "Async GEE calls found — the app is sync-via-thread only:\n" + result.stdout
    )
