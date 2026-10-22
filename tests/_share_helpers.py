"""A small real creator project, and a review copy, for the share tests
(spec 2026-10-10 §5, §6): an AOI with a sidecar, two periods with one set
each, the first set's GeoParquet store split between evaluators, a scene
index that records Planet access, a report template and every kind of vis
layer. Plus zip helpers to read and tamper with a package."""

import json
import zipfile
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    AoiRecord,
    Artifact,
    EvaluatorSplit,
    FileVisLayer,
    GeeVisLayer,
    Period,
    Project,
    Question,
    QuestionOption,
    ReviewCopy,
    SourceCoverage,
    add_predefined_source,
    default_questions,
    period_id,
    set_id,
)
from deforestation_alerts.project.share import canonical_set_paths
from deforestation_alerts.scripts import scene_index
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet

NOW = datetime(2026, 10, 1, 12, 0)
P1 = Period(id=period_id(date(2026, 1, 1), date(2026, 3, 31)), start=date(2026, 1, 1), end=date(2026, 3, 31))
P2 = Period(id=period_id(date(2026, 4, 1), date(2026, 6, 30)), start=date(2026, 4, 1), end=date(2026, 6, 30))
SET1 = set_id(P1.id, ["GLAD-L"])  # "2026-01-01_2026-03-31__glad-l"
SET2 = set_id(P2.id, ["GLAD-L"])
SPLIT_ID = "a" * 32
CREATOR = "Creator proj"
APP_VERSION = "9.9.9-test"
AOI_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {"name": "aoi"},
    "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]},
}]}
CAUSE = Question(key="cause", label="Cause", options=[QuestionOption(code="fire", label="Fire")])


def alerts_frame(statuses, evaluators=None, *, sid=SET1, extra=None):
    """A store as Integrate writes it (``engine/integrate.py:410-423``), with
    the split's ``evaluator`` column (left out when ``evaluators`` is None)
    and ``extra`` columns."""
    n = len(statuses)
    data = {"alert_id": list(range(n)), "set_id": [sid] * n, "status": list(statuses),
            "description": [""] * n, "area_ha": [1.5] * n}
    if evaluators is not None:
        data["evaluator"] = list(evaluators)
    data.update(extra or {})
    gdf = gpd.GeoDataFrame(data, geometry=gpd.GeoSeries(
        [box(i * 0.1, 0.0, i * 0.1 + 0.05, 0.05) for i in range(n)], crs="EPSG:4326"))
    gdf["alert_polygon"] = gpd.GeoSeries([None] * n, crs="EPSG:4326")
    return gdf


def alert_set(sid, period, store_rel, split=None, n=4) -> AlertSet:
    return AlertSet(id=sid, period_id=period.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                    params=AlertSetParams(), store_rel_path=store_rel, combined_rel_path="legacy/combined.tif",
                    footprint_rel_path="legacy/footprint.tif", n_alerts=n, fingerprint=f"fp-{sid}",
                    created_at=NOW, split=split)


def write_imagery(pdir, *, access=True) -> None:
    """The scene index's three files, as ``scene_cache.write_index`` leaves
    them, with the creator's Planet access recorded."""
    scenes = pd.DataFrame([(scene_index.SOURCE_S2, pd.Timestamp("2026-05-10"), 10.0, "W", "k1")],
                          columns=list(scene_index.SCENE_COLUMNS))
    tiles = gpd.GeoDataFrame({"tile": ["W"]}, geometry=gpd.GeoSeries([box(0, 0, 2, 2)], crs=scene_index.WGS84),
                             crs=scene_index.WGS84)
    manifest = scene_index.SceneManifest(
        aoi_digest="aoi-digest", refreshed=NOW,
        covered=scene_index.Coverage(start=date(2026, 5, 1), end=date(2026, 5, 31)),
        counts={scene_index.SOURCE_S2: 1}, planet=scene_index.PlanetState(region="americas", access=access))
    paths.imagery_dir(pdir).mkdir(parents=True, exist_ok=True)
    tiles.to_parquet(paths.tiles_path(pdir), index=False)
    scenes.to_parquet(paths.scenes_path(pdir), index=False)
    paths.scene_manifest_path(pdir).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def _aoi(sidecar: bool) -> AoiRecord:
    return AoiRecord(spec={"method": "DRAW"}, name="aoi", method="DRAW", digest="aoi-digest",
                     bounds=(0.0, 0.0, 1.0, 1.0), geometry_file=paths.AOI_SIDECAR if sidecar else None)


def make_creator(root, name=CREATOR, *, evaluators=("Ana", "Bo"), assigned=("Ana", "Bo", "Ana", "Bo"),
                 statuses=("Not reviewed",) * 4, sidecar=True, template=True, imagery=True):
    """The creator's project saved under ``root``; returns ``(project, pdir)``.
    Set 1 (the shared one) keeps its store at a non-canonical legacy path, so
    a test sees the builder rewrite it; set 2 is another period's."""
    pdir = paths.project_dir(name, root)
    pdir.mkdir(parents=True)
    p = Project(name=name, created_at=NOW, updated_at=NOW)
    add_predefined_source(p, "GLAD-L")
    p.aoi = _aoi(sidecar)
    if sidecar:
        (pdir / paths.AOI_SIDECAR).write_text(json.dumps(AOI_FC), encoding="utf-8")
    p.periods = {P1.id: P1, P2.id: P2}
    p.active_period = P2.id
    p.vis_layers = {"gee": GeeVisLayer(name="gee", asset="users/x/img"),
                    "file": FileVisLayer(name="file", path="/abs/creator/layer.tif")}
    p.coverage = {"GLAD-L": SourceCoverage(aoi_digest="aoi-digest", checked_on=date(2026, 10, 1), fraction=1.0)}
    p.artifacts = {"store:x": Artifact(key="store:x", kind="store", rel_path="legacy/alert_db.parquet",
                                       fingerprint="f", inputs={}, content_id="c", created_at=NOW)}
    p.questions = [*default_questions(), CAUSE]
    split = EvaluatorSplit(split_id=SPLIT_ID, evaluators=list(evaluators), created_at=NOW)
    p.alert_sets = {
        SET1: alert_set(SET1, P1, "legacy/alert_db.parquet", split, n=len(statuses)),
        SET2: alert_set(SET2, P2, f"periods/{P2.id}/sets/glad-l/alert_db.parquet"),
    }
    p.active_set = SET1
    save_gdf_to_parquet(alerts_frame(statuses, assigned), pdir / "legacy" / "alert_db.parquet")
    if template:
        tpl = pdir / "my_template.docx"
        tpl.write_bytes(b"not really a docx")
        p.report_template = str(tpl)
    if imagery:
        write_imagery(pdir)
    io.save_project(p, root)
    return p, pdir


def make_copy(root, name="creator-proj__e01-ana", *, statuses, assigned, extra=None, evaluator="Ana", index=1):
    """A review copy as an import leaves it (canonical store path,
    ``review_copy`` set), saved under ``root``; returns ``(project, pdir)``."""
    pdir = paths.project_dir(name, root)
    pdir.mkdir(parents=True)
    p = Project(name=name, created_at=NOW, updated_at=NOW)
    add_predefined_source(p, "GLAD-L")
    p.aoi = _aoi(True)
    (pdir / paths.AOI_SIDECAR).write_text(json.dumps(AOI_FC), encoding="utf-8")
    p.periods = {P1.id: P1}
    p.active_period = P1.id
    p.questions = [*default_questions(), CAUSE]
    split = EvaluatorSplit(split_id=SPLIT_ID, evaluators=["Ana", "Bo"], created_at=NOW, packaged=True)
    s = alert_set(SET1, P1, "", split, n=len(statuses))
    s = s.model_copy(update=canonical_set_paths(s, P1))
    p.alert_sets = {SET1: s}
    p.active_set = SET1
    p.review_copy = ReviewCopy(evaluator=evaluator, evaluator_index=index, set_id=SET1, split_id=SPLIT_ID,
                               set_fingerprint=s.fingerprint, origin_project=CREATOR, imported_at=NOW)
    save_gdf_to_parquet(alerts_frame(statuses, assigned, extra=extra), paths.resolve(pdir, s.store_rel_path))
    io.save_project(p, root)
    return p, pdir


def write_zip(path, members: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def zip_members(path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def rezip(src, dst, *, replace=None, drop=()) -> Path:
    """``src`` rewritten to ``dst`` with members dropped, replaced or added."""
    members = {k: v for k, v in zip_members(src).items() if k not in drop}
    members.update(replace or {})
    return write_zip(dst, members)


#: The growth creator's earlier set (SET1, P1): two loss verdicts, a false
#: positive and an unreviewed alert (spec 2026-10-20 §6.6).
EARLIER_STATUSES = ("Confirmed", "False Positive", "Need revision", "Not reviewed")


def make_growth_creator(root, name=CREATOR, *, links=None, earlier_links=None, statuses=EARLIER_STATUSES):
    """A creator whose later set SET2 (P2) is split between Ana and Bo and
    active, its alerts ``links`` (``{alert_id: [link, ...]}``) into SET1, whose
    store holds ``statuses`` and ``earlier_links``. Returns ``(project, pdir)``."""
    from deforestation_alerts.project.growth import run_id_of

    p, pdir = make_creator(root, name, statuses=statuses, assigned=("Ana", "Bo") * 2, template=False, imagery=False)
    run1 = run_id_of(p.alert_sets[SET1])
    store1 = pdir / "legacy" / "alert_db.parquet"
    earlier = alerts_frame(statuses, ("Ana", "Bo") * 2)
    earlier["grows_from"] = [[lk.format(run=run1) for lk in (earlier_links or {}).get(i, [])]
                             for i in range(len(statuses))]
    save_gdf_to_parquet(earlier, store1)
    split = EvaluatorSplit(split_id="b" * 32, evaluators=["Ana", "Bo"], created_at=NOW)
    s2 = p.alert_sets[SET2].model_copy(update={"split": split})
    p.alert_sets[SET2] = s2
    p.active_set = SET2
    later = alerts_frame(("Not reviewed",) * 4, ("Ana", "Bo") * 2, sid=SET2)
    later["grows_from"] = [[lk.format(run=run1) for lk in (links or {}).get(i, [])] for i in range(4)]
    later["grows_from_auto"] = [list(v) for v in later["grows_from"]]
    save_gdf_to_parquet(later, paths.resolve(pdir, s2.store_rel_path))
    io.save_project(p, root)
    return p, pdir
