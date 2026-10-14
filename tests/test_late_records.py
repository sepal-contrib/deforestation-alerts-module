"""Two late results for the same closed project both persist (spec §14 slice 2)."""

import threading
import time
import uuid
from datetime import datetime

from deforestation_alerts.project import io
from deforestation_alerts.project.models import Artifact, Project


def _art(key):
    return Artifact(key=key, kind="raw_alert", rel_path=f"{key}.tif", fingerprint="f", inputs={},
                    content_id=uuid.uuid4().hex, created_at=datetime.now())


def test_two_late_records_into_one_closed_project_both_persist(tmp_path, monkeypatch):
    io.save_project(Project(name="late", created_at=datetime.now(), updated_at=datetime.now()), tmp_path)
    real_load = io.load_project

    def slow_load(name, root=None):
        p = real_load(name, root)
        time.sleep(0.2)  # both writers read before either saves, unless the lock serialises them
        return p

    monkeypatch.setattr(io, "load_project", slow_load)
    errors = []

    def record(key):
        try:
            io.update_saved_project("late", lambda p: setattr(p, "artifacts", {**p.artifacts, key: _art(key)}),
                                    root=tmp_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=record, args=(k,)) for k in ("raw:a", "raw:b")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert set(real_load("late", tmp_path).artifacts) == {"raw:a", "raw:b"}


def test_update_saved_project_skips_when_apply_returns_false(tmp_path):
    io.save_project(Project(name="skip", created_at=datetime.now(), updated_at=datetime.now()), tmp_path)
    before = (tmp_path / "skip" / "project.json").read_text()
    assert io.update_saved_project("skip", lambda p: False, root=tmp_path) is False
    assert (tmp_path / "skip" / "project.json").read_text() == before


def test_one_lock_per_folder(tmp_path):
    assert io.manifest_lock("a", tmp_path) is io.manifest_lock("a", tmp_path)
    assert io.manifest_lock("a", tmp_path) is not io.manifest_lock("b", tmp_path)
