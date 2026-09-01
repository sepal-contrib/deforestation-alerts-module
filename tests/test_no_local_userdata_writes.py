"""User data must flow through the Workspace adapter (SepalClient), never
directly to the container filesystem (pysepal-app User File Rules)."""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# category-(b)/(c) files from the inventory
# (docs/superpowers/plans/2026-08-31-user-file-inventory.md) are exempt by design
EXEMPT = {
    # (c1) the scratch machinery itself: `probe.write_text("")` is the write
    # probe that proves a container-local directory is usable. It is also the one
    # place (c3) that must NOT be pointed back at the user's workspace.
    "deforestation_alerts/gdal_env.py",
    # (a) the adapter: its `write_text` IS the sanctioned boundary every user
    # file goes through, and its `scratch_*` half is the container-local staging.
    "deforestation_alerts/scripts/workspace.py",
    # grep noise, not filesystem access: the inventory's "Not filesystem access"
    # section pins both of these to a `Project.save()` mention in a DOCSTRING
    # (project_writers.py:4, solara_threads.py:86).
    "gui/store/project_writers.py",
    "gui/scripts/solara_threads.py",
}


def test_no_direct_userdata_filesystem_calls():
    result = subprocess.run(
        ["grep", "-rln", r"to_parquet(\|to_file(\|to_csv(\|\.save(\|write_text(",
         str(REPO / "deforestation_alerts"), str(REPO / "gui"), "--include=*.py"],
        capture_output=True, text=True,
    )
    offenders = [
        p for p in result.stdout.splitlines()
        if Path(p).relative_to(REPO).as_posix() not in EXEMPT
        and "/tests/" not in p
        and "workspace.upload_from_scratch" not in Path(p).read_text()
    ]
    assert offenders == [], f"direct user-data writes outside the adapter: {offenders}"
