"""Importing a review package (spec 2026-10-10 §6.1, §6.5, §9): payload
validation before anything is written, importer-derived destinations, the
folder name and its collisions, the Planet reset, rollback, and finding an
earlier copy of the same split and evaluator."""

import json

import pytest

from deforestation_alerts.project import paths, share
from deforestation_alerts.project.share import (
    ShareError,
    build_package,
    build_return,
    find_existing_copy,
    import_package,
    peek_header,
)
from deforestation_alerts.scripts import scene_index
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet
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
    rezip,
    zip_members,
)

CANON = "periods/2026-01-01_2026-03-31/sets/glad-l/alert_db.parquet"


@pytest.fixture
def evaluator_root(tmp_path):
    return tmp_path / "evaluator"  # another SEPAL account's data directory


@pytest.fixture
def package(data_root, tmp_path):
    p, pdir = make_creator(data_root)
    return build_package(p, pdir, SET1, "Ana", AOI_FC, tmp_path / "out", app_version=APP_VERSION)


def _refused(zip_path, root) -> ShareError:
    with pytest.raises(ShareError) as e:
        import_package(zip_path, root)
    assert not root.exists() or not any(root.iterdir()), "a refused import left files behind"
    return e.value


def _with_header(package, dst, **over):
    header = json.loads(zip_members(package)["share.json"])
    header.update(over)
    return rezip(package, dst, replace={"share.json": json.dumps(header)})


def _with_manifest(package, dst, change):
    manifest = json.loads(zip_members(package)["project.json"])
    change(manifest)
    return rezip(package, dst, replace={"project.json": json.dumps(manifest)})


def _store_bytes(tmp_path, gdf) -> bytes:
    path = tmp_path / "store" / "alert_db.parquet"
    save_gdf_to_parquet(gdf, path)
    return path.read_bytes()


# ---- the copy -----------------------------------------------------------------------

def test_an_imported_package_is_a_review_copy(package, evaluator_root):
    p = import_package(package, evaluator_root)
    folder = evaluator_root / "creator-proj__e01-ana"
    assert p.name == folder.name and (folder / "project.json").is_file()
    rc = p.review_copy
    assert (rc.evaluator, rc.evaluator_index, rc.set_id, rc.split_id) == ("Ana", 1, SET1, SPLIT_ID)
    assert (rc.set_fingerprint, rc.origin_project) == (f"fp-{SET1}", CREATOR)
    assert list(p.alert_sets) == [SET1] and p.active_set == SET1
    assert list(p.periods) == [P1.id] and p.active_period == P1.id
    assert p.artifacts == {} and p.coverage == {} and set(p.vis_layers) == {"gee"}
    assert p.alert_sets[SET1].store_rel_path == CANON
    assert p.alert_sets[SET1].split.split_id == SPLIT_ID and p.alert_sets[SET1].split.packaged is True
    assert load_gdf_from_parquet(folder / CANON)["evaluator"].tolist() == ["Ana", "Bo", "Ana", "Bo"]
    assert p.aoi.geometry_file == "aoi.geojson" and p.aoi.digest == "aoi-digest"
    aoi = json.loads((folder / "aoi.geojson").read_text(encoding="utf-8"))
    assert aoi["features"][0]["geometry"] == AOI_FC["features"][0]["geometry"]


def test_the_default_root_is_the_data_directory(package, data_root):
    p = import_package(package)
    assert (data_root / p.name / "project.json").is_file()


def test_the_template_is_imported_into_the_copy(package, evaluator_root):
    p = import_package(package, evaluator_root)
    template = evaluator_root / p.name / "template" / "report.docx"
    assert p.report_template == str(template.resolve()) and template.read_bytes() == b"not really a docx"


def test_a_package_without_template_or_imagery_imports_without_them(data_root, tmp_path, evaluator_root):
    p, pdir = make_creator(data_root, template=False, imagery=False)
    out = build_package(p, pdir, SET1, "Ana", AOI_FC, tmp_path / "out", app_version=APP_VERSION)
    copy = import_package(out, evaluator_root)
    assert copy.report_template is None
    assert not paths.imagery_dir(evaluator_root / copy.name).exists()


def test_the_scene_index_is_reused_with_planet_access_reset(package, data_root, evaluator_root):
    p = import_package(package, evaluator_root)
    folder, creator_dir = evaluator_root / p.name, paths.project_dir(CREATOR, data_root)
    original = scene_index.SceneManifest.model_validate_json(paths.scene_manifest_path(creator_dir).read_text())
    copied = scene_index.SceneManifest.model_validate_json(paths.scene_manifest_path(folder).read_text())
    assert original.planet.access is True and copied.planet == scene_index.PlanetState()
    assert copied.model_copy(update={"planet": original.planet}) == original
    assert paths.scenes_path(folder).read_bytes() == paths.scenes_path(creator_dir).read_bytes()
    assert paths.tiles_path(folder).read_bytes() == paths.tiles_path(creator_dir).read_bytes()


def test_an_unreadable_scene_index_is_left_out(package, tmp_path, evaluator_root):
    p = import_package(rezip(package, tmp_path / "bad.zip", replace={"imagery/index.json": b"{nope"}), evaluator_root)
    assert not paths.imagery_dir(evaluator_root / p.name).exists()


def test_manifest_paths_are_never_trusted(package, tmp_path, evaluator_root):
    def escape(m):
        m["alert_sets"][SET1]["store_rel_path"] = "../../stolen.parquet"
        m["report_template"] = "/etc/passwd"

    p = import_package(_with_manifest(package, tmp_path / "bad.zip", escape), evaluator_root)
    assert p.alert_sets[SET1].store_rel_path == CANON and (evaluator_root / p.name / CANON).is_file()
    assert p.report_template == str((evaluator_root / p.name / "template" / "report.docx").resolve())
    assert not (tmp_path / "stolen.parquet").exists()


# ---- names ----------------------------------------------------------------------------

def test_importing_twice_makes_a_second_copy_and_finds_the_first(package, evaluator_root):
    header = peek_header(package)
    assert find_existing_copy(header, evaluator_root) is None
    first = import_package(package, evaluator_root)
    assert find_existing_copy(header, evaluator_root) == first.name
    second = import_package(package, evaluator_root)
    assert (first.name, second.name) == ("creator-proj__e01-ana", "creator-proj__e01-ana-2")
    assert find_existing_copy(header, evaluator_root) == first.name


def test_a_folder_of_that_name_in_another_case_is_not_reused(package, evaluator_root):
    (evaluator_root / "Creator-Proj__E01-Ana").mkdir(parents=True)  # no manifest: still taken
    assert import_package(package, evaluator_root).name == "creator-proj__e01-ana-2"


def test_evaluators_whose_names_share_a_token_get_their_own_folders(data_root, tmp_path, evaluator_root):
    p, pdir = make_creator(data_root, evaluators=("Ana Maria", "Ana-Maria"),
                           assigned=("Ana Maria", "Ana-Maria") * 2)
    names = [import_package(build_package(p, pdir, SET1, e, AOI_FC, tmp_path / "out", app_version=APP_VERSION),
                            evaluator_root).name for e in ("Ana Maria", "Ana-Maria")]
    assert names == ["creator-proj__e01-ana-maria", "creator-proj__e02-ana-maria"]


def test_find_existing_copy_ignores_other_splits_and_evaluators(package, evaluator_root):
    import_package(package, evaluator_root)
    header = peek_header(package)
    assert find_existing_copy(header.model_copy(update={"split_id": "b" * 32}), evaluator_root) is None
    assert find_existing_copy(header.model_copy(update={"evaluator": "Bo"}), evaluator_root) is None
    assert find_existing_copy(header, evaluator_root / "nowhere") is None


# ---- refusals ----------------------------------------------------------------------------

def test_a_reviewed_copy_is_refused_with_its_own_message(data_root, tmp_path, evaluator_root):
    p, pdir = make_copy(data_root, statuses=["Confirmed"], assigned=["Ana"])
    returned = build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)
    assert _refused(returned, evaluator_root).key == "share.err.is_return"


def test_an_unknown_kind_is_refused(package, tmp_path, evaluator_root):
    bad = _with_header(package, tmp_path / "bad.zip", kind="review-something")
    assert _refused(bad, evaluator_root).key == "share.err.unknown_kind"


def test_a_file_that_is_not_a_zip_is_refused(tmp_path, evaluator_root):
    path = tmp_path / "x.zip"
    path.write_text("hello")
    assert _refused(path, evaluator_root).key == "share.err.not_a_zip"


def test_a_member_with_an_escaping_name_is_refused(package, tmp_path, evaluator_root):
    bad = rezip(package, tmp_path / "bad.zip", replace={"../evil.txt": b"x"})
    assert _refused(bad, evaluator_root).key == "share.err.unsafe_member"
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("header, what", [
    ({"set_id": "2026-01-01_2026-03-31__radd"}, "set_id"),
    ({"set_fingerprint": "other"}, "set_fingerprint"),
    ({"split_id": "b" * 32}, "split_id"),
    ({"evaluator": "Bo"}, "evaluator"),  # index 1 is Ana
    ({"evaluator_index": 3}, "evaluator"),
    ({"evaluator_index": 0}, "evaluator"),
])
def test_a_header_that_does_not_match_the_manifest_is_refused(package, tmp_path, evaluator_root, header, what):
    exc = _refused(_with_header(package, tmp_path / "bad.zip", **header), evaluator_root)
    assert (exc.key, exc.params) == ("share.err.payload_mismatch", {"what": what})


def _second_set(m):
    m["alert_sets"]["other"] = {**m["alert_sets"][SET1], "id": "other"}


def _escaping_period(m):
    s, period = m["alert_sets"][SET1], m["periods"].pop(P1.id)
    s["period_id"] = period["id"] = "../../evil"
    m["periods"]["../../evil"] = period


def _foreign_sources(m):
    m["alert_sets"][SET1]["sources"] = ["RADD"]


def _no_aoi(m):
    m["aoi"] = None


@pytest.mark.parametrize("change, what", [(_second_set, "set_id"), (_escaping_period, "set_layout"),
                                          (_foreign_sources, "set_layout"), (_no_aoi, "aoi")])
def test_a_manifest_that_does_not_hold_the_headers_set_is_refused(package, tmp_path, evaluator_root, change, what):
    exc = _refused(_with_manifest(package, tmp_path / "bad.zip", change), evaluator_root)
    assert (exc.key, exc.params) == ("share.err.payload_mismatch", {"what": what})


@pytest.mark.parametrize("data", [b"{nope", b"[]", json.dumps({"name": "x"}).encode()])
def test_an_unreadable_manifest_is_refused(package, tmp_path, evaluator_root, data):
    bad = rezip(package, tmp_path / "bad.zip", replace={"project.json": data})
    assert _refused(bad, evaluator_root).key == "share.err.bad_manifest"


def test_a_manifest_of_another_schema_is_refused(package, tmp_path, evaluator_root):
    bad = _with_manifest(package, tmp_path / "bad.zip", lambda m: m.update(schema_version=2))
    assert _refused(bad, evaluator_root).key == "share.err.bad_manifest"


@pytest.mark.parametrize("data", [b"{}", b"nope", json.dumps({"type": "FeatureCollection", "features": []}).encode()])
def test_an_unreadable_aoi_is_refused(package, tmp_path, evaluator_root, data):
    bad = rezip(package, tmp_path / "bad.zip", replace={"aoi.geojson": data})
    assert _refused(bad, evaluator_root).key == "share.err.bad_aoi"


STORES = {
    "no evaluator column": lambda: alerts_frame(["Not reviewed"] * 2),
    "no status column": lambda: alerts_frame(["Not reviewed"] * 2, ["Ana", "Bo"]).drop(columns="status"),
    "duplicate ids": lambda: alerts_frame(["Not reviewed"] * 2, ["Ana", "Bo"]).assign(alert_id=[1, 1]),
    "text ids": lambda: alerts_frame(["Not reviewed"] * 2, ["Ana", "Bo"]).assign(alert_id=["a", "b"]),
    "another set": lambda: alerts_frame(["Not reviewed"] * 2, ["Ana", "Bo"], sid="another-set"),
}


@pytest.mark.parametrize("make", list(STORES.values()), ids=list(STORES))
def test_a_store_that_is_not_the_sets_is_refused(package, tmp_path, evaluator_root, make):
    bad = rezip(package, tmp_path / "bad.zip", replace={"alerts/alert_db.parquet": _store_bytes(tmp_path, make())})
    assert _refused(bad, evaluator_root).key == "share.err.bad_store"


def test_a_store_that_is_not_parquet_is_refused(package, tmp_path, evaluator_root):
    bad = rezip(package, tmp_path / "bad.zip", replace={"alerts/alert_db.parquet": b"garbage"})
    assert _refused(bad, evaluator_root).key == "share.err.bad_store"


# ---- rollback and destinations --------------------------------------------------------------

def test_a_failure_while_writing_removes_the_claimed_folder(package, evaluator_root, monkeypatch):
    def full_disk(p, root=None):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(share.project_io, "save_project", full_disk)
    with pytest.raises(ShareError) as e:
        import_package(package, evaluator_root)
    assert e.value.key == "share.err.import_failed" and "No space left" in e.value.params["error"]
    assert list(evaluator_root.iterdir()) == []


def test_a_destination_outside_the_folder_is_refused(tmp_path):
    folder = tmp_path / "copy"
    folder.mkdir()
    assert share._inside(folder, "imagery/index.json") == (folder / "imagery" / "index.json").resolve()
    for rel in ("../x", "/etc/passwd", ".", "a/../../x"):
        with pytest.raises(ShareError) as e:
            share._inside(folder, rel)
        assert (e.value.key, e.value.params) == ("share.err.unsafe_member", {"member": rel})


# ---- the earlier alerts (spec 2026-10-20 §6.6) ----------------------------------------------

@pytest.fixture
def growth_package(data_root, tmp_path):
    from gui.scripts import growth_index

    growth_index.clear_cache()
    p, pdir = make_growth_creator(data_root)
    yield build_package(p, pdir, SET2, "Ana", AOI_FC, tmp_path / "out", app_version=APP_VERSION)
    growth_index.clear_cache()


def test_copy_reads_snapshot(growth_package, tmp_path):
    from deforestation_alerts.project.growth import read_snapshot
    from gui.scripts import growth_index

    copy = import_package(growth_package)  # the data directory: growth_index reads the copy from there
    snap = paths.project_dir(copy.name) / paths.EARLIER_SNAPSHOT
    assert paths.EARLIER_SNAPSHOT == "earlier_alerts.parquet" and snap.is_file()
    (tmp_path / "m.parquet").write_bytes(zip_members(growth_package)[share.EARLIER_MEMBER])
    assert snap.read_bytes() == (tmp_path / "m.parquet").read_bytes()
    assert copy.review_copy.snapshot_at == peek_header(growth_package).created_at
    assert growth_index.candidates_for(copy, SET2) == read_snapshot(snap)
    assert [c.key for c in growth_index.candidates_for(copy, SET2)] == [f"{SET1}#0", f"{SET1}#2"]


def test_old_package_without_snapshot_imports(growth_package, tmp_path):
    from gui.scripts import growth_index

    old = rezip(growth_package, tmp_path / "old.zip", drop=(share.EARLIER_MEMBER,))
    copy = import_package(old)
    assert copy.review_copy.snapshot_at is None
    assert not (paths.project_dir(copy.name) / paths.EARLIER_SNAPSHOT).exists()
    assert growth_index.candidates_for(copy, SET2) == []


def test_an_unreadable_snapshot_is_refused(growth_package, tmp_path, evaluator_root):
    bad = rezip(growth_package, tmp_path / "bad.zip", replace={share.EARLIER_MEMBER: b"garbage"})
    assert _refused(bad, evaluator_root).key == "share.err.bad_store"
