"""Contract tests for the SepalClient workspace adapter.

The fake these run against lives in ``tests/conftest.py`` (the ``ws`` /
``fakes`` fixtures) because every store test now needs it too. It mirrors the
REAL ``pysepal_api.SepalClient`` surface the adapter binds to --
``client.files.{list,read_bytes,read_json,write,mkdir}`` plus the pure
``client.results_path`` attribute -- including the parts that bite:

* ``files.list`` returns a ``DirectoryListing`` model (not a list of names), so
  the fake builds the REAL pydantic models rather than hand-rolled stand-ins: a
  drift in that model breaks these tests instead of production.
* ``files.write`` defaults ``overwrite=False`` and raises ``Conflict`` when the
  file exists, so a second save to the same path fails unless the adapter opts
  in. ``test_write_twice_overwrites`` pins that it does.
* ``ensure_results_dir()`` is network I/O the client's own docstring says to
  call once at startup, never on a render path. ``results_path`` is pure path
  arithmetic. ``test_results_dir_is_pure`` pins that the adapter builds paths
  from the latter and never calls the former. (The app makes that one startup
  call in ``Page()``, not from inside the adapter.)
"""

from pathlib import Path

import pytest
from pysepal_api.errors import Conflict, NotFound


# ---------------------------------------------------------------------------
# Remote path arithmetic
# ---------------------------------------------------------------------------


def test_results_dir_roots_under_results_path(ws, fakes):
    p = ws.results_dir("recipes", "r1")
    assert p == f"{fakes.RESULTS_PATH}/recipes/r1"
    assert p.startswith(str(fakes.RESULTS_PATH))


def test_results_dir_no_parts_is_the_root(ws, fakes):
    assert ws.results_dir() == str(fakes.RESULTS_PATH)
    assert ws.root == str(fakes.RESULTS_PATH)


def test_results_dir_is_pure(ws):
    """No network on the path-building call: safe on a UI render path."""
    ws.results_dir("recipes", "r1")
    assert ws.client.ensure_calls == 0
    assert ws.client.files.mkdir_calls == []


def test_results_dir_rejects_escaping_parts(ws):
    with pytest.raises(ValueError):
        ws.results_dir("..", "etc")
    with pytest.raises(ValueError):
        ws.results_dir("/etc")


def test_workspace_requires_a_client():
    from deforestation_alerts.scripts.workspace import Workspace

    with pytest.raises(ValueError):
        Workspace(None)


def test_workspace_requires_a_module_name(fakes):
    from deforestation_alerts.scripts.workspace import Workspace

    with pytest.raises(ValueError):
        Workspace(fakes.FakeSepalClient(module_name=None))


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def test_write_read_roundtrip(ws):
    path = ws.results_dir("recipes") + "/x.json"
    ws.write_bytes(path, b"{}")
    assert ws.read_bytes(path) == b"{}"


def test_write_creates_the_parent_directory_once(ws):
    """Writes materialize their folder, but only one mkdir per folder."""
    folder = ws.results_dir("recipes", "r1")
    ws.write_bytes(folder + "/a.json", b"1")
    ws.write_bytes(folder + "/b.json", b"2")
    assert ws.client.files.mkdir_calls == [folder]
    assert ws.client.ensure_calls == 0


def test_write_to_a_bare_relative_path_creates_no_folder(ws):
    """A dirless parent (".") is nothing to create -- no pointless round trip."""
    ws.write_bytes("x.json", b"{}")
    assert ws.client.files.mkdir_calls == []
    assert ws.read_bytes("x.json") == b"{}"


def test_write_twice_overwrites(ws):
    """The real client defaults overwrite=False; re-saving a recipe must work."""
    path = ws.results_dir("recipes") + "/x.json"
    ws.write_bytes(path, b"one")
    ws.write_bytes(path, b"two")
    assert ws.read_bytes(path) == b"two"


def test_write_can_refuse_to_overwrite(ws):
    path = ws.results_dir("recipes") + "/x.json"
    ws.write_bytes(path, b"one")
    with pytest.raises(Conflict):
        ws.write_bytes(path, b"two", overwrite=False)


def test_text_and_json_roundtrip(ws):
    folder = ws.results_dir("recipes", "r1")
    ws.write_text(folder + "/note.txt", "hola")
    assert ws.read_text(folder + "/note.txt") == "hola"
    ws.write_json(folder + "/recipe_parameters.json", {"a": 1, "b": [2, 3]})
    assert ws.read_json(folder + "/recipe_parameters.json") == {"a": 1, "b": [2, 3]}


def test_text_roundtrip_is_utf8_symmetric(ws):
    """write_text encodes UTF-8, so read_text must decode UTF-8 -- not whatever
    charset httpx guesses from a response SEPAL may send without one. Recipe and
    alert names are user-supplied free text and the UI ships en / es-ES / fr, so
    this is the normal case, not an edge case."""
    path = ws.results_dir("recipes") + "/nombre.txt"
    name = "Canindeyú — Región Ñuble, forêt dégradée, 森林"
    ws.write_text(path, name)
    assert ws.read_text(path) == name
    # And the bytes on the wire really are UTF-8, not some other encoding.
    assert ws.read_bytes(path) == name.encode("utf-8")


def test_json_roundtrip_survives_non_ascii(ws):
    path = ws.results_dir("recipes") + "/recipe_parameters.json"
    params = {"recipe_name": "Canindeyú_2026", "note": "déforestation"}
    ws.write_json(path, params)
    assert ws.read_json(path) == params


def test_read_missing_raises(ws):
    with pytest.raises(NotFound):
        ws.read_bytes(ws.results_dir("nope.txt"))


# ---------------------------------------------------------------------------
# Listing / existence
# ---------------------------------------------------------------------------


def test_list_returns_paths_under_the_folder(ws):
    folder = ws.results_dir("recipes", "r1")
    ws.write_bytes(folder + "/a.json", b"1")
    ws.write_bytes(folder + "/b.parquet", b"2")
    assert ws.list(folder) == [folder + "/a.json", folder + "/b.parquet"]


def test_list_defaults_to_the_results_root_and_splits_dirs_from_files(ws):
    ws.write_bytes(ws.results_dir("r1") + "/a.json", b"1")
    ws.write_bytes(ws.results_dir("top.txt"), b"x")
    assert ws.list(dirs_only=True) == [ws.results_dir("r1")]
    assert ws.list(files_only=True) == [ws.results_dir("top.txt")]


def test_list_filters_by_extension(ws):
    """UNVERIFIED against a live SEPAL server: whether `listFiles` also drops
    *directories* when `extensions` is set is decided here by FakeUserFiles
    (which filters files only and always keeps directories), not by anything
    real -- pysepal just forwards the parameter. If the server drops
    directories too, a caller combining `extensions=` with `dirs_only=` gets
    fewer results than this test implies. No call site does that today; check
    it against a real session before one does."""
    folder = ws.results_dir("recipes")
    ws.write_bytes(folder + "/a.json", b"1")
    ws.write_bytes(folder + "/b.parquet", b"2")
    assert ws.list(folder, extensions=[".json"]) == [folder + "/a.json"]


def test_list_of_a_missing_folder_is_empty(ws):
    """The 404 this rests on is FakeUserFiles' choice here, but it was confirmed
    against sepal.io on 2026-09-01 (Task 11 live check; see Workspace.list's
    note). A deployment answering 403/500 for a missing folder would still make
    the production call raise while this test passes -- the signature-drift
    guard below compares signatures, not response semantics."""
    assert ws.list(ws.results_dir("does_not_exist")) == []


def test_exists(ws):
    """Carries the same 404 assumption as the listing tests above; see
    Workspace.exists' note for what breaks where it does not hold."""
    path = ws.results_dir("recipes") + "/x.json"
    assert ws.exists(path) is False
    ws.write_bytes(path, b"{}")
    assert ws.exists(path) is True
    assert ws.exists(ws.results_dir("recipes")) is True


def test_home_relative_strips_the_sepal_home(ws):
    """What DownloadBtn.set_url must be handed; see Workspace.home_relative."""
    assert ws.home_relative(ws.results_dir("r1", "a.gpkg")) == (
        "module_results/deforestation_alerts/r1/a.gpkg"
    )
    assert ws.home_relative("module_results/x") == "module_results/x"


def test_home_relative_rejects_paths_outside_the_sepal_home(ws):
    from pysepal_api.errors import InvalidPathError

    with pytest.raises(InvalidPathError):
        ws.home_relative("/etc/passwd")


def test_ensure_dir_is_memoized(ws):
    a = ws.ensure_dir("recipes", "r1")
    b = ws.ensure_dir("recipes", "r1")
    assert a == b == ws.results_dir("recipes", "r1")
    assert ws.client.files.mkdir_calls == [a]


# ---------------------------------------------------------------------------
# Scratch staging
# ---------------------------------------------------------------------------


def test_scratch_file_is_local_unique_and_not_created(ws, tmp_path):
    a = ws.scratch_file(".tif")
    b = ws.scratch_file(".tif")
    assert a != b
    assert a.suffix == ".tif"
    assert not a.exists()
    assert a.parent.is_dir()
    assert str(a).startswith(str(tmp_path / "scratch"))


def test_scratch_dir_is_fresh_and_empty(ws):
    d1, d2 = ws.scratch_dir(), ws.scratch_dir()
    assert d1 != d2
    assert d1.is_dir() and list(d1.iterdir()) == []


def test_download_to_scratch_keeps_the_remote_basename(ws):
    remote = ws.results_dir("recipes", "r1") + "/alert_db.parquet"
    ws.write_bytes(remote, b"PAR1")
    local = ws.download_to_scratch(remote)
    assert local.name == "alert_db.parquet"
    assert local.read_bytes() == b"PAR1"


def test_download_to_scratch_does_not_collide_on_equal_names(ws):
    for recipe in ("r1", "r2"):
        ws.write_bytes(
            ws.results_dir(recipe) + "/alert_db.parquet", recipe.encode()
        )
    a = ws.download_to_scratch(ws.results_dir("r1") + "/alert_db.parquet")
    b = ws.download_to_scratch(ws.results_dir("r2") + "/alert_db.parquet")
    assert a != b
    assert a.read_bytes() == b"r1" and b.read_bytes() == b"r2"


def test_upload_from_scratch_roundtrip(ws):
    local = ws.scratch_file(".gpkg")
    local.write_bytes(b"GPKG")
    remote = ws.results_dir("recipes", "r1") + "/alert_0.gpkg"
    assert ws.upload_from_scratch(local, remote) == remote
    assert ws.read_bytes(remote) == b"GPKG"
    assert local.exists()  # the caller still owns the staged file


def test_upload_from_scratch_accepts_a_str_path(ws):
    local = ws.scratch_file(".txt")
    local.write_text("hi")
    remote = ws.results_dir("t.txt")
    ws.upload_from_scratch(str(local), remote)
    assert ws.read_text(remote) == "hi"


def test_cleanup_scratch_removes_everything_and_is_reusable(ws):
    a = ws.scratch_file(".txt")
    a.write_text("x")
    root = a.parent.parent
    ws.cleanup_scratch()
    assert not root.exists()
    b = ws.scratch_file(".txt")  # a later call re-creates the root
    b.write_text("y")
    assert b.exists()


def test_cleanup_scratch_is_a_noop_when_unused(ws):
    ws.cleanup_scratch()  # must not raise


def test_scratch_never_touches_the_container_cwd(ws, tmp_path):
    """Scratch lives under gdal_env.scratch_dir(), which is never the CWD."""
    assert Path.cwd() not in ws.scratch_file().parents


# ---------------------------------------------------------------------------
# The fake is only worth anything while it matches the real client
# ---------------------------------------------------------------------------


def _shape(func):
    """(name, kind, default) per parameter -- signature identity minus annotations."""
    import inspect

    return [
        (p.name, p.kind, p.default) for p in inspect.signature(func).parameters.values()
    ]


def test_fake_user_files_matches_the_real_endpoint_signatures(fakes):
    from pysepal_api.endpoints.user_files import UserFilesEndpoint

    for name in ("list", "read_bytes", "read_text", "read_json", "write", "mkdir"):
        assert _shape(getattr(fakes.FakeUserFiles, name)) == _shape(
            getattr(UserFilesEndpoint, name)
        ), f"FakeUserFiles.{name} has drifted from UserFilesEndpoint.{name}"


def test_real_client_exposes_the_attributes_the_adapter_binds_to(fakes):
    """Construction is pure (no network), so this can assert on the real class."""
    from pysepal_api import NoAuth, SepalClient

    with SepalClient(
        module_name="deforestation_alerts",
        base_url="https://example.invalid",
        auth=NoAuth(),
    ) as client:
        assert client.results_path == fakes.RESULTS_PATH
        assert hasattr(client.files, "write") and hasattr(client.files, "mkdir")


def test_workspace_accepts_the_real_client(fakes):
    from deforestation_alerts.scripts.workspace import Workspace
    from pysepal_api import NoAuth, SepalClient

    with SepalClient(
        module_name="deforestation_alerts",
        base_url="https://example.invalid",
        auth=NoAuth(),
    ) as client:
        assert Workspace(client).results_dir("recipes") == f"{fakes.RESULTS_PATH}/recipes"
