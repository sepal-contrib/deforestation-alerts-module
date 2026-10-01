import threading
from pathlib import Path

import pytest

from deforestation_alerts.scripts.workspace import Workspace


@pytest.fixture
def local_ws(tmp_path):
    return Workspace(root=tmp_path / "data", scratch_parent=tmp_path / "scratch")


def test_results_dir_and_ensure_dir(local_ws, tmp_path):
    assert local_ws.results_dir("p", "sets") == str(tmp_path / "data" / "p" / "sets")
    with pytest.raises(ValueError):
        local_ws.results_dir("../escape")
    made = local_ws.ensure_dir("p", "x")
    assert Path(made).is_dir()


def test_write_read_round_trip_creates_parents(local_ws, tmp_path):
    target = tmp_path / "data" / "p" / "a" / "b.json"
    local_ws.write_json(str(target), {"k": 1})
    assert local_ws.read_json(str(target)) == {"k": 1}
    assert local_ws.exists(str(target))
    with pytest.raises(FileExistsError):
        local_ws.write_text(str(target), "x", overwrite=False)


def test_download_to_scratch_returns_the_file_itself(local_ws, tmp_path):
    f = tmp_path / "data" / "user.tif"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"123")
    staged = local_ws.download_to_scratch(str(f))
    assert staged == f
    local_ws.discard_scratch(staged)  # must NOT delete a user file
    assert f.exists()


def test_upload_copies_and_scratch_is_discardable(local_ws, tmp_path):
    local = local_ws.scratch_file(name="out.gpkg")
    local.write_bytes(b"abc")
    dest = tmp_path / "data" / "p" / "out.gpkg"
    assert local_ws.upload_from_scratch(local, str(dest)) == str(dest)
    assert dest.read_bytes() == b"abc"
    local_ws.discard_scratch(local)
    assert not local.exists()


def test_list_filters_extensions_and_hidden(local_ws, tmp_path):
    d = tmp_path / "data" / "p"
    d.mkdir(parents=True)
    for name in ("a.tif", "b.json", ".hidden.tif"):
        (d / name).write_text("x")
    assert local_ws.list(str(d), extensions=[".tif"]) == [str(d / "a.tif")]
    assert local_ws.list(str(tmp_path / "missing")) == []


def test_home_relative(local_ws):
    p = Path.home() / "module_results" / "x.zip"
    assert local_ws.home_relative(str(p)) == "module_results/x.zip"


@pytest.mark.parametrize("outside", ["/var/tmp/x.zip", "{home}/../elsewhere/x.zip"])
def test_home_relative_rejects_a_path_outside_home(local_ws, tmp_path, monkeypatch, outside):
    """A download link is only valid under the user's home: Analysis Download
    and the Overview export rely on this refusal, never on a ``../`` link."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(ValueError):
        local_ws.home_relative(outside.format(home=home))


def test_cleanup_scratch(local_ws):
    d = local_ws.scratch_dir()
    (d / "f").write_text("1")
    local_ws.cleanup_scratch()
    assert not d.exists()


def test_tmp_sibling_is_unique_per_call(tmp_path):
    """Regression: write_bytes/upload_from_scratch used to build the tmp
    sibling from `dest.name` alone, so two concurrent writers targeting the
    same destination shared one tmp file. `_tmp_sibling` must hand back a
    distinct path every time, even for the same destination."""
    dest = tmp_path / "data" / "p" / "x.json"
    a = Workspace._tmp_sibling(dest)
    b = Workspace._tmp_sibling(dest)
    assert a != b
    assert a.parent == dest.parent
    assert b.parent == dest.parent


def test_concurrent_writes_to_the_same_destination_do_not_corrupt_it(
    local_ws, tmp_path
):
    """Regression for the fixed-name `.part` race: two threads writing
    different, same-length payloads to the SAME destination at once must
    each write to their own tmp file. The loser of the final atomic rename
    is simply overwritten (last-writer-wins is unchanged and fine), but the
    file on disk must always be exactly one writer's complete payload --
    never a byte-level mix of both, and never truncated -- and no stray
    `.part` file should be left behind afterwards.
    """
    dest = tmp_path / "data" / "shared.bin"
    payload_a = b"A" * 200_000
    payload_b = b"B" * 200_000
    start = threading.Barrier(2)

    def write(payload):
        start.wait()
        local_ws.write_bytes(str(dest), payload)

    threads = [
        threading.Thread(target=write, args=(payload_a,)),
        threading.Thread(target=write, args=(payload_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = dest.read_bytes()
    assert result in (payload_a, payload_b)
    assert list(dest.parent.glob("*.part")) == []


def test_create_only_write_is_atomic_under_concurrency(local_ws, tmp_path):
    """Regression for the `overwrite=False` TOCTOU: a `dest.exists()` check
    followed by a separate write-and-rename leaves a window where two
    concurrent create-only writers can both see "does not exist" and both
    "succeed", silently defeating the create-only guarantee. With the fix
    (an atomic `os.link` into place), exactly one of two simultaneous
    create-only writers to the same destination must win; the other must
    see `FileExistsError`, deterministically, every time.
    """
    dest = tmp_path / "data" / "once.bin"
    start = threading.Barrier(2)
    results: list[tuple[str, bytes]] = []
    lock = threading.Lock()

    def attempt(payload):
        start.wait()
        try:
            local_ws.write_bytes(str(dest), payload, overwrite=False)
            outcome = "ok"
        except FileExistsError:
            outcome = "exists"
        with lock:
            results.append((outcome, payload))

    threads = [
        threading.Thread(target=attempt, args=(b"A",)),
        threading.Thread(target=attempt, args=(b"B",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    oks = [payload for outcome, payload in results if outcome == "ok"]
    assert len(oks) == 1
    assert dest.read_bytes() == oks[0]
    assert list(dest.parent.glob("*.part")) == []
