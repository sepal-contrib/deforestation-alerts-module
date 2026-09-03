"""Shared pytest fixtures for the deforestation-alerts test suite.

Phase 0 tests are pure: no Earth Engine calls, no UI rendering. We deliberately
do NOT call init_ee() here so the suite runs offline in CI without credentials.

The fake ``SepalClient`` below is the suite-wide stand-in for the session client
that ``Workspace`` (``deforestation_alerts/scripts/workspace.py``) binds to. It
lives here rather than in ``test_workspace.py`` because every store test now runs
against ``Workspace(FakeSepalClient())``.

**Fixtures are the only sharing channel.** An installed ``tests`` package in
site-packages shadows this directory, so ``from tests.conftest import ...``
resolves to the wrong module; the ``fakes`` fixture hands the classes to the few
tests that need them by name (signature-drift guards, constructor error paths).
"""

# Reacton's first ``t()`` call *during* a first render corrupts its internal
# widget map and surfaces later as a ``use_event`` KeyError (only reproduces in
# isolated single-test runs). Warm the translator once at collection time so
# every render test (``test_pipeline_header`` mounts ``rv.use_event`` rows) is
# covered. Must be import-time, not an autouse fixture: conftest is imported
# before any test module and its component imports.
from gui.i18n import t as _warm_t  # noqa: E402

_warm_t("common.cancel")

import types
from pathlib import PurePosixPath

import pytest
from pysepal_api.errors import Conflict, NotFound
from pysepal_api.models import DirectoryListing, FileEntry, FileWriteResult

RESULTS_PATH = PurePosixPath("/home/sepal-user/module_results/deforestation_alerts")


class FakeUserFiles:
    """In-memory stand-in for ``pysepal_api.endpoints.user_files.UserFilesEndpoint``.

    Signatures copied from the real class (and pinned against it by
    ``test_workspace.test_fake_user_files_matches_the_real_endpoint_signatures``);
    paths are kept verbatim (the real endpoint sanitizes them server-side, which
    is not what these tests are about).
    """

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.mkdir_calls: list[str] = []

    # -- real signature: list(folder=".", *, extensions=None, include_hidden=False)
    def list(self, folder=".", *, extensions=None, include_hidden=False):
        folder = str(folder).rstrip("/") or "/"
        if folder not in self.dirs:
            raise NotFound(404, url=folder)
        entries = []
        for name in self._children(folder, self.dirs):
            entries.append(
                FileEntry(name=name, path=f"{folder}/{name}", type="directory", size=0)
            )
        for name in self._children(folder, self.files):
            if extensions and PurePosixPath(name).suffix not in extensions:
                continue
            entries.append(
                FileEntry(
                    name=name,
                    path=f"{folder}/{name}",
                    type="file",
                    size=len(self.files[f"{folder}/{name}"]),
                )
            )
        if not include_hidden:
            entries = [e for e in entries if not e.name.startswith(".")]
        return DirectoryListing(path=folder, files=entries, count=len(entries))

    @staticmethod
    def _children(folder, keys):
        prefix = folder if folder.endswith("/") else folder + "/"
        out = []
        for key in keys:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if "/" in rest or not rest:
                continue
            out.append(rest)
        return out

    def read_bytes(self, file_path):
        key = str(file_path)
        if key not in self.files:
            raise NotFound(404, url=key)
        return self.files[key]

    def read_text(self, file_path):
        return self.read_bytes(file_path).decode("utf-8")

    def read_json(self, file_path):
        import json

        return json.loads(self.read_text(file_path))

    # -- real signature: write(file_path, content, *, overwrite=False)
    def write(self, file_path, content, *, overwrite=False):
        key = str(file_path)
        if key in self.files and not overwrite:
            raise Conflict(409, url=key)
        payload = content.encode("utf-8") if isinstance(content, str) else content
        self.files[key] = payload
        return FileWriteResult(path=key, size=len(payload))

    # -- real signature: mkdir(path, *, parents=True) -> PurePosixPath
    def mkdir(self, path, *, parents=True):
        key = str(path).rstrip("/") or "/"
        self.mkdir_calls.append(key)
        parts = PurePosixPath(key).parts
        for i in range(1, len(parts) + 1):
            self.dirs.add(str(PurePosixPath(*parts[:i])).rstrip("/") or "/")
        return PurePosixPath(key)


class FakeSepalClient:
    """Fake of the session ``SepalClient``: a pure ``results_path`` plus ``files``.

    ``ensure_results_dir`` is present because the real client has it -- it counts
    calls so a test can prove the adapter never issues that request. Nothing in
    the app calls it either: ``Page()`` builds the Workspace on a render path,
    and writes create their own parents.
    """

    def __init__(self, module_name="deforestation_alerts"):
        self.module_name = module_name
        self.results_path = RESULTS_PATH if module_name else None
        self.files = FakeUserFiles()
        self.ensure_calls = 0

    def ensure_results_dir(self):
        self.ensure_calls += 1
        if not self.module_name:
            return None
        return self.files.mkdir(str(self.results_path), parents=True)


@pytest.fixture()
def fakes():
    """The fake classes + the results-root constant, for tests that need them by
    name rather than through the ``ws`` fixture (see the module docstring for why
    this is a fixture and not an import)."""
    return types.SimpleNamespace(
        FakeSepalClient=FakeSepalClient,
        FakeUserFiles=FakeUserFiles,
        RESULTS_PATH=RESULTS_PATH,
    )


@pytest.fixture()
def ws(monkeypatch, tmp_path):
    """A ``Workspace`` over a fake client, with scratch pinned inside tmp_path.

    The single seam every store test uses: user files land in the fake's
    in-memory workspace (reachable as ``ws.client.files``), and the local files
    the parquet/CSV libraries insist on land under tmp_path.
    """
    from deforestation_alerts.scripts.workspace import Workspace

    monkeypatch.setenv("DEFORESTATION_ALERTS_GDAL_TMPDIR", str(tmp_path / "scratch"))
    return Workspace(FakeSepalClient())


@pytest.fixture()
def sample_wkt_csv(tmp_path):
    """Write a tiny CSV with a WKT geometry column and return its path.

    Mirrors the shape of the recipe `alert_db.csv` that `load_gdf_from_csv`
    reads: a geometry column stored as WKT strings plus a plain attribute column.
    """
    csv_path = tmp_path / "alert_db.csv"
    csv_path.write_text(
        "id,point\n"
        "0,POINT (1 2)\n"
        "1,\n"  # empty geometry -> should decode to None
    )
    return csv_path


@pytest.fixture(scope="session")
def gee():
    """Initialize Earth Engine once; skip the module if unauthenticated."""
    try:
        from pysepal.scripts.utils import init_ee
        init_ee()
        import ee
        ee.Number(1).getInfo()  # force a round-trip to confirm creds
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Earth Engine unavailable: {exc}")
    return True


@pytest.fixture
def tiny_aoi(gee):
    import ee
    return ee.Geometry.Rectangle([-55.10, -10.10, -55.00, -10.00])  # ~Amazon
