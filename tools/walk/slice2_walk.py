"""Slice 2 gate walk (spec §14 row 2): review one alert, save it, Overview shows
"1 reviewed", one Analysis download and one Overview export.

    python -m tools.walk.slice2_walk --port 8981 --out /tmp/slice2_walk
"""

import argparse
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.seed_project import seed

PROJECT = "slice2_walk"
BOX = (-55.10, -10.10, -55.00, -10.00)  # Amazon, GLAD-L and RADD alerts in 2025-2026


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8981)
    ap.add_argument("--out", type=Path, default=Path("/tmp/slice2_walk"))
    args = ap.parse_args(argv)
    data = Path.home() / "module_results" / "deforestation_alerts_walk_slice2"
    # The walk's own data dir, rebuilt: files a previous run left (a set's
    # downloads) would hide what this run writes.
    shutil.rmtree(data, ignore_errors=True)
    seed(PROJECT, BOX, sources=("GLAD-L", "RADD"), period=("2025-06-01", "2026-08-31"), look_back=0, root=data)
    args.out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, data, args.out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)
            out = args.out
            folder = lambda: ph.set_dir(data, PROJECT)  # noqa: E731
            ok = ph.run_phases(page, [
                ("open", lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT))),
                ("prepare", lambda pg, r: ph.prepare_grid_and_run_all(pg, r, out=out)),
                ("integrate", lambda pg, r: ph.integrate_and_land(pg, r, out=out)),
                ("review_save", lambda pg, r: ph.review_and_save(pg, r, out=out)),
                ("pick_images", lambda pg, r: ph.pick_images(pg, r, out=out)),
                ("download", lambda pg, r: ph.download_with_images(pg, r, out=out, folder=folder())),
                ("overview_reviewed", lambda pg, r: ph.overview_reviewed(pg, r, out=out, n=1)),
                ("overview_export", lambda pg, r: ph.overview_export_csv(pg, r, out=out, folder=folder())),
            ], report, args.out / "report.json")
            report["page_errors_count"] = len(report.get("page_errors", []))
            browser.close()
    finally:
        w.stop_server(proc)
    print("WALK", "PASS" if ok else "FAIL", args.out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
