"""Building a review package and a reviewed copy (spec 2026-10-10 §5,
§6.4): the trimmed manifest, the exact members, the AOI, the snapshot under
the store and scene-index locks, and the return's rows and columns."""

import json
import os
import threading
from pathlib import Path

import pandas as pd
import pytest

from deforestation_alerts.project import paths, share
from deforestation_alerts.project import split as split_mod
from deforestation_alerts.project.models import GeeVisLayer
from deforestation_alerts.project.share import (
    KIND_PACKAGE,
    KIND_RETURN,
    PACKAGE_OPTIONAL,
    PACKAGE_REQUIRED,
    RETURN_REQUIRED,
    ShareError,
    aoi_geojson_for_package,
    build_package,
    build_return,
    canonical_set_paths,
    open_checked,
    read_header,
    read_table,
    return_rows,
    trimmed_manifest,
)
from deforestation_alerts.scripts import alert_store, scene_index
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from tests._share_helpers import (
    AOI_FC,
    APP_VERSION,
    CREATOR,
    P1,
    SET1,
    SET2,
    SPLIT_ID,
    alerts_frame,
    make_copy,
    make_creator,
    make_growth_creator,
    zip_members,
)

SDIR = "periods/2026-01-01_2026-03-31/sets/glad-l"


@pytest.fixture
def creator(data_root):
    return make_creator(data_root)


def _build(p, pdir, out, evaluator="Ana", aoi=AOI_FC, set_id=SET1):
    return build_package(p, pdir, set_id, evaluator, aoi, out, app_version=APP_VERSION)


def _refused(call) -> ShareError:
    with pytest.raises(ShareError) as e:
        call()
    return e.value


# ---- the trimmed manifest -------------------------------------------------------

def test_the_trimmed_manifest_keeps_only_the_shared_set_and_its_period(creator):
    p, _ = creator
    t = trimmed_manifest(p, SET1)
    assert list(t.alert_sets) == [SET1] and t.active_set == SET1
    assert list(t.periods) == [P1.id] and t.active_period == P1.id
    assert t.alert_sets[SET1].split.split_id == SPLIT_ID
    assert t.sources.keys() == p.sources.keys() and t.questions == p.questions
    assert t.grid == p.grid and t.look_back_days == p.look_back_days


def test_the_trimmed_manifest_drops_artifacts_coverage_file_layers_and_the_template(creator):
    p, _ = creator
    t = trimmed_manifest(p, SET1)
    assert t.artifacts == {} and t.coverage == {}
    assert set(t.vis_layers) == {"gee"} and isinstance(t.vis_layers["gee"], GeeVisLayer)
    assert t.report_template is None and t.review_copy is None
    assert t.aoi.geometry_file == "aoi.geojson" and t.aoi.digest == "aoi-digest"


def test_the_trimmed_manifest_rewrites_the_set_paths_to_the_canonical_layout(creator):
    p, _ = creator
    canonical = {"store_rel_path": f"{SDIR}/alert_db.parquet",
                 "combined_rel_path": f"{SDIR}/filtered_alerts_glad-l_2026-01-01_2026-03-31.tif",
                 "footprint_rel_path": f"{SDIR}/footprint.tif"}
    s = trimmed_manifest(p, SET1).alert_sets[SET1]
    assert {k: getattr(s, k) for k in canonical} == canonical
    assert canonical_set_paths(p.alert_sets[SET1], P1) == canonical


def test_the_trimmed_manifest_leaves_the_project_as_it_was(creator):
    p, _ = creator
    before = p.model_dump()
    trimmed_manifest(p, SET1)
    assert p.model_dump() == before


def test_the_trimmed_manifest_of_an_unknown_set_is_refused(creator):
    exc = _refused(lambda: trimmed_manifest(creator[0], "nope"))
    assert (exc.key, exc.params) == ("share.err.no_set", {"set_id": "nope"})


# ---- the package ------------------------------------------------------------------

def test_a_package_holds_exactly_the_allowed_members(creator, tmp_path):
    p, pdir = creator
    out = _build(p, pdir, tmp_path / "out")
    assert out == tmp_path / "out" / f"creator-proj__{SET1}__e01-ana__review.zip"
    with open_checked(out, PACKAGE_REQUIRED, PACKAGE_OPTIONAL) as zf:
        assert set(zf.namelist()) == PACKAGE_REQUIRED | PACKAGE_OPTIONAL - {share.EARLIER_MEMBER}  # no earlier set
    members = zip_members(out)
    assert members["alerts/alert_db.parquet"] == (pdir / "legacy" / "alert_db.parquet").read_bytes()
    assert members["template/report.docx"] == b"not really a docx"
    for name in ("scenes.parquet", "tiles.parquet", "index.json"):
        assert members[f"imagery/{name}"] == (pdir / "imagery" / name).read_bytes()


def test_a_package_without_imagery_or_template_leaves_them_out(data_root, tmp_path):
    p, pdir = make_creator(data_root, template=False, imagery=False)
    with open_checked(_build(p, pdir, tmp_path / "out"), PACKAGE_REQUIRED, PACKAGE_OPTIONAL) as zf:
        assert set(zf.namelist()) == PACKAGE_REQUIRED


def test_a_partial_scene_index_is_left_out(creator, tmp_path):
    p, pdir = creator
    paths.tiles_path(pdir).unlink()
    assert not [n for n in zip_members(_build(p, pdir, tmp_path / "out")) if n.startswith("imagery/")]


def test_the_package_header(creator, tmp_path):
    p, pdir = creator
    with open_checked(_build(p, pdir, tmp_path / "out", evaluator="Bo"), PACKAGE_REQUIRED, PACKAGE_OPTIONAL) as zf:
        h = read_header(zf)
    assert (h.kind, h.app_version, h.origin_project, h.set_id) == (KIND_PACKAGE, APP_VERSION, CREATOR, SET1)
    assert (h.set_fingerprint, h.split_id, h.evaluator, h.evaluator_index) == (f"fp-{SET1}", SPLIT_ID, "Bo", 2)
    assert h.questions_digest == split_mod.questions_digest(p.questions) and h.returned_at is None


def test_the_packaged_manifest_records_the_split_as_packaged_and_the_project_is_untouched(creator, tmp_path):
    p, pdir = creator
    manifest = json.loads(zip_members(_build(p, pdir, tmp_path / "out"))["project.json"])
    split = manifest["alert_sets"][SET1]["split"]
    assert split["packaged"] is True and split["questions_digest"] == split_mod.questions_digest(p.questions)
    assert list(manifest["alert_sets"]) == [SET1] and manifest["artifacts"] == {}
    assert p.alert_sets[SET1].split.packaged is False  # the caller records it (T12)


def test_the_aoi_is_written_as_a_plain_feature_collection(creator, tmp_path):
    p, pdir = creator
    aoi = json.loads(zip_members(_build(p, pdir, tmp_path / "out"))["aoi.geojson"])
    assert aoi["type"] == "FeatureCollection" and len(aoi["features"]) == 1
    assert aoi["features"][0]["properties"] == {}
    assert aoi["features"][0]["geometry"] == AOI_FC["features"][0]["geometry"]


def test_aoi_geojson_for_package_reads_the_sidecar(creator):
    p, pdir = creator
    assert aoi_geojson_for_package(p, pdir) == AOI_FC


def test_an_earth_engine_aoi_without_a_sidecar_is_packaged_from_the_given_geometry(data_root, tmp_path):
    p, pdir = make_creator(data_root, sidecar=False)
    assert aoi_geojson_for_package(p, pdir) is None
    ee_info = {"type": "FeatureCollection", "columns": {}, "features": [
        {"type": "Feature", "id": "0", "properties": {},
         "geometry": {"type": "Polygon", "geodesic": False, "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]]}}]}
    members = zip_members(_build(p, pdir, tmp_path / "out", aoi=ee_info))
    assert json.loads(members["aoi.geojson"])["features"][0]["geometry"] == {
        "type": "Polygon", "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 0.0]]]}
    assert json.loads(members["project.json"])["aoi"]["geometry_file"] == "aoi.geojson"


@pytest.mark.parametrize("aoi", [None, {}, {"type": "FeatureCollection", "features": []},
                                 {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None}]}])
def test_a_package_without_an_aoi_geometry_is_refused(creator, tmp_path, aoi):
    p, pdir = creator
    assert _refused(lambda: _build(p, pdir, tmp_path / "out", aoi=aoi)).key == "share.err.no_aoi"
    assert not (tmp_path / "out").exists()


def test_a_package_for_an_unknown_evaluator_is_refused(creator, tmp_path):
    p, pdir = creator
    exc = _refused(lambda: _build(p, pdir, tmp_path / "out", evaluator="Cy"))
    assert (exc.key, exc.params) == ("share.err.unknown_evaluator", {"evaluator": "Cy"})


def test_a_package_of_a_set_without_a_split_is_refused(creator, tmp_path):
    p, pdir = creator
    assert _refused(lambda: _build(p, pdir, tmp_path / "out", set_id=SET2)).key == "share.err.no_split"


def test_a_package_of_a_missing_store_is_refused(creator, tmp_path):
    p, pdir = creator
    (pdir / "legacy" / "alert_db.parquet").unlink()
    assert _refused(lambda: _build(p, pdir, tmp_path / "out")).key == "share.err.store_missing"


def test_a_store_assigned_to_a_name_outside_the_split_is_refused(data_root, tmp_path):
    p, pdir = make_creator(data_root, assigned=("Ana", "Bo", "Cy", "Bo"))
    assert _refused(lambda: _build(p, pdir, tmp_path / "out")).key == "share.err.split_mismatch"


def test_a_store_without_an_evaluator_column_is_refused(creator, tmp_path):
    p, pdir = creator
    save_gdf_to_parquet(alerts_frame(["Not reviewed"] * 4), pdir / "legacy" / "alert_db.parquet")
    assert _refused(lambda: _build(p, pdir, tmp_path / "out")).key == "share.err.split_mismatch"


# ---- the snapshot ---------------------------------------------------------------------

def _held(lock) -> bool:
    """Whether another thread holds ``lock`` right now, probed from a fresh
    thread (``store_lock`` is an RLock, which has no ``locked()``)."""
    out = []

    def probe():
        got = lock.acquire(blocking=False)
        if got:
            lock.release()
        out.append(not got)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return out[0]


def test_the_store_and_the_scene_index_are_copied_under_their_locks(creator, tmp_path, monkeypatch):
    p, pdir = creator
    real = share._copy_file
    held = {}

    def spy(src, dest):
        src = Path(src)
        if src.name == "alert_db.parquet":
            held["store"] = _held(alert_store.store_lock(src))
        else:
            held[src.name] = _held(scene_index.index_lock(pdir))
        return real(src, dest)

    monkeypatch.setattr(share, "_copy_file", spy)
    _build(p, pdir, tmp_path / "out")
    assert held == {"store": True, "scenes.parquet": True, "tiles.parquet": True, "index.json": True}
    assert not _held(alert_store.store_lock(pdir / "legacy" / "alert_db.parquet"))
    assert not _held(scene_index.index_lock(pdir))


def test_a_store_rewritten_during_the_copy_is_refused(creator, tmp_path, monkeypatch):
    p, pdir = creator
    real = share._copy_file

    def rewritten_meanwhile(src, dest):
        real(src, dest)
        if Path(src).name == "alert_db.parquet":  # an Integrate swap lands meanwhile (it takes no store lock)
            st = os.stat(src)
            os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(share, "_copy_file", rewritten_meanwhile)
    exc = _refused(lambda: _build(p, pdir, tmp_path / "out"))
    assert (exc.key, exc.params) == ("share.err.store_changed", {"set_id": SET1})
    assert not (tmp_path / "out").exists()


def test_the_scene_cache_lock_is_the_scene_index_lock(data_root):
    from deforestation_alerts.scripts import scene_cache

    pdir = paths.project_dir("p")
    assert scene_cache._lock_for(pdir) is scene_index.index_lock(pdir)
    assert scene_index.index_lock(pdir / "x" / "..") is scene_index.index_lock(pdir)
    assert scene_index.index_lock(paths.project_dir("q")) is not scene_index.index_lock(pdir)


# ---- the reviewed copy ---------------------------------------------------------------------

STATUSES = ["Confirmed", "Confirmed", "", "Not reviewed", "False Positive", None]
ASSIGNED = ["Ana", "Bo", "Ana", "Ana", "Ana", ""]


@pytest.fixture
def copy(data_root):
    return make_copy(data_root, statuses=STATUSES, assigned=ASSIGNED,
                     extra={"q_cause": ["fire"] * 6, "admin1": ["Chaco"] * 6, "notes": ["x"] * 6})


def test_return_rows_are_the_evaluators_reviewed_alerts(copy):
    p, pdir = copy
    gdf = alert_store.load_gdf_from_parquet(paths.resolve(pdir, p.alert_sets[SET1].store_rel_path))
    rows = return_rows(gdf, "Ana", p.questions)
    assert rows["alert_id"].tolist() == [0, 4]
    assert list(rows.columns) == ["alert_id", "evaluator", "status", "description", "q_cause", "admin1",
                                  "alert_polygon", "area_ha"]


def test_a_reviewed_copy_holds_the_header_and_the_rows(copy, tmp_path):
    p, pdir = copy
    out = build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)
    assert out == tmp_path / "out" / f"creator-proj__{SET1}__e01-ana__reviewed.zip"
    with open_checked(out, RETURN_REQUIRED) as zf:
        h = read_header(zf)
        (tmp_path / "r.parquet").write_bytes(zf.read("reviews.parquet"))
    assert (h.kind, h.evaluator, h.evaluator_index, h.split_id, h.set_id) == (KIND_RETURN, "Ana", 1, SPLIT_ID, SET1)
    assert (h.origin_project, h.set_fingerprint, h.app_version) == (CREATOR, f"fp-{SET1}", APP_VERSION)
    assert h.returned_at is not None and h.questions_digest == split_mod.questions_digest(p.questions)
    rows = read_table(tmp_path / "r.parquet")
    assert rows["alert_id"].tolist() == [0, 4] and rows["status"].tolist() == ["Confirmed", "False Positive"]
    assert rows["q_cause"].tolist() == ["fire", "fire"] and "notes" not in rows.columns
    assert rows["alert_polygon"].isna().all()


def test_a_reviewed_copy_with_nothing_reviewed_yet_has_no_rows(data_root, tmp_path):
    p, pdir = make_copy(data_root, statuses=["Not reviewed"] * 2, assigned=["Ana", "Bo"])
    out = build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)
    (tmp_path / "r.parquet").write_bytes(zip_members(out)["reviews.parquet"])
    assert len(read_table(tmp_path / "r.parquet")) == 0


def test_only_a_review_copy_returns_reviews(creator, tmp_path):
    p, pdir = creator
    exc = _refused(lambda: build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION))
    assert exc.key == "share.err.not_a_copy"


def test_read_table_reads_a_plain_parquet(tmp_path):
    pd.DataFrame({"alert_id": [1], "status": ["Confirmed"]}).to_parquet(tmp_path / "plain.parquet")
    assert read_table(tmp_path / "plain.parquet")["alert_id"].tolist() == [1]


# ---- the earlier alerts (spec 2026-10-20 §6.6) -----------------------------------

def _snapshot(zip_path, tmp_path):
    from deforestation_alerts.project.growth import read_snapshot

    dest = tmp_path / "snap.parquet"
    dest.write_bytes(zip_members(zip_path)[share.EARLIER_MEMBER])
    return read_snapshot(dest)


def test_package_carries_earlier_alerts(data_root, tmp_path):
    from deforestation_alerts.project.growth import run_id_of

    p, pdir = make_growth_creator(data_root)
    out = _build(p, pdir, tmp_path / "out", set_id=SET2)
    assert share.EARLIER_MEMBER == "earlier_alerts.parquet" and share.EARLIER_MEMBER in share.PACKAGE_OPTIONAL
    with open_checked(out, PACKAGE_REQUIRED, PACKAGE_OPTIONAL) as zf:
        assert share.EARLIER_MEMBER in zf.namelist()
    rows = _snapshot(out, tmp_path)
    run1 = run_id_of(p.alert_sets[SET1])
    assert [(r.key, r.node.status, r.run_id) for r in rows] == [
        (f"{SET1}#0", "Confirmed", run1), (f"{SET1}#2", "Need revision", run1)]
    n = rows[0].node
    assert (n.period_id, n.period_start, n.period_end, n.area_ha) == (P1.id, P1.start, P1.end, 1.5)
    assert n.shape.equals(alerts_frame(["x"]).geometry.iloc[0])


def test_package_snapshot_holds_every_linked_row_whatever_its_status(data_root, tmp_path):
    """Ruling 12: a later alert linked to a since false-positive parent, and a
    candidate linked to an unreviewed one, keep their targets in the copy."""
    p, pdir = make_growth_creator(data_root, links={0: [f"{SET1}#1@{{run}}"]},
                                  earlier_links={0: [f"{SET1}#3@{{run}}"]})
    rows = _snapshot(_build(p, pdir, tmp_path / "out", set_id=SET2), tmp_path)
    assert [(r.key, r.node.status) for r in rows] == [
        (f"{SET1}#0", "Confirmed"), (f"{SET1}#2", "Need revision"),
        (f"{SET1}#1", "False Positive"), (f"{SET1}#3", "Not reviewed")]


def test_a_package_with_no_earlier_loss_has_no_snapshot(data_root, tmp_path):
    p, pdir = make_growth_creator(data_root, statuses=("Not reviewed", "False Positive", "Not reviewed", "Not reviewed"))
    assert share.EARLIER_MEMBER not in zip_members(_build(p, pdir, tmp_path / "out", set_id=SET2))


def test_a_return_carries_the_links(data_root, tmp_path):
    p, pdir = make_copy(data_root, statuses=["Confirmed", "Not reviewed"], assigned=["Ana", "Ana"],
                        extra={"grows_from": [["a#1@r"], []]})
    rows = return_rows(alert_store.load_gdf_from_parquet(paths.resolve(pdir, p.alert_sets[SET1].store_rel_path)),
                       "Ana", p.questions)
    assert list(rows.columns)[-1] == "grows_from" and list(rows["grows_from"].iloc[0]) == ["a#1@r"]
