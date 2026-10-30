"""The user guide's screenshot script (tools/docs/capture.py), without a browser."""

import hashlib
import json
import random
import re
from pathlib import Path

import pytest
from PIL import Image

from tools.docs.capture import LANGS, MARKER, SHOTS, TYPED, copy_demo, optimize_png
from tools.walk import phases as ph

REPO = Path(__file__).resolve().parents[1]
_SHOT = re.compile(r"^\.\. shot:\s*([a-z0-9-]+)\s*\|", re.MULTILINE)


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file()}


def _demo(root: Path) -> Path:
    project = root / "madre_de_dios"
    (project / "periods" / "p" / "sets").mkdir(parents=True)
    (project / "project.json").write_text('{"name": "madre_de_dios"}', encoding="utf-8")
    (project / "periods" / "p" / "sets" / "alerts.parquet").write_bytes(b"\x00" * 64)
    (root / "_fixtures").mkdir()
    (root / "_fixtures" / "x__review.zip").write_bytes(b"zip")
    return project


def test_copy_demo_leaves_source_untouched(tmp_path):
    src = tmp_path / "demo"
    project = _demo(src)
    before = _snapshot(src)
    copied = copy_demo(project, tmp_path / "data")
    assert copied == tmp_path / "data" / "madre_de_dios"
    assert (copied / "project.json").read_text(encoding="utf-8") == '{"name": "madre_de_dios"}'
    assert (tmp_path / "data" / "_fixtures" / "x__review.zip").is_file()
    (copied / "project.json").write_text("{}", encoding="utf-8")
    (copied / "periods" / "p" / "new.txt").write_text("x", encoding="utf-8")
    (tmp_path / "data" / "_fixtures" / "x__review.zip").unlink()
    assert _snapshot(src) == before
    # the data dir of the build works too, and a second copy starts afresh
    again = copy_demo(src, tmp_path / "data")
    assert (again / "project.json").read_text(encoding="utf-8") == '{"name": "madre_de_dios"}'
    assert not (again / "periods" / "p" / "new.txt").exists()
    assert _snapshot(src) == before


def test_every_doc_shot_has_a_capture():
    ids = set(_SHOT.findall((REPO / "doc" / "en.rst").read_text(encoding="utf-8")))
    assert ids, "no shot comments found"
    assert sorted(ids - set(SHOTS)) == [], "shots without a capture"
    assert sorted(set(SHOTS) - ids) == [], "captures no page uses"


def test_optimize_png_shrinks_and_keeps_size(tmp_path):
    path = tmp_path / "shot.png"
    img = Image.new("RGB", (640, 400))
    for x in range(0, 640, 4):  # a few flat colours, the way an app screenshot is
        for y in range(0, 400, 50):
            img.putpixel((x, y), (x % 256, y % 256, 120))
    img.save(path, compress_level=0)
    before = path.stat().st_size
    optimize_png(path)
    assert path.stat().st_size < before
    with Image.open(path) as out:
        assert out.size == (640, 400)


def test_optimize_png_falls_back_to_a_palette(tmp_path):
    """Still over the limit after zlib: quantized to 256 colours, same size."""
    path = tmp_path / "noisy.png"
    rnd = random.Random(1)
    img = Image.new("RGB", (300, 200))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(300 * 200)])
    img.save(path)
    optimize_png(path, limit=1024)
    with Image.open(path) as out:
        assert out.mode == "P" and out.size == (300, 200)


def test_copy_demo_refuses_a_folder_it_did_not_make(tmp_path):
    project = _demo(tmp_path / "demo")
    other = tmp_path / "data"
    other.mkdir()
    (other / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ValueError, match="not made by capture.py"):
        copy_demo(project, other)
    assert (other / "keep.txt").read_text(encoding="utf-8") == "mine"
    (other / MARKER).write_text("", encoding="utf-8")  # one it made is wiped
    copy_demo(project, other)
    assert not (other / "keep.txt").exists()


@pytest.mark.parametrize("where", ["inside", "same", "around"])
def test_copy_demo_refuses_to_overlap_the_demo(tmp_path, where):
    root = tmp_path / "demo"
    project = _demo(root)
    before = _snapshot(root)
    data = {"inside": root / "copy", "same": root, "around": tmp_path}[where]
    with pytest.raises(ValueError, match="overlaps the demo"):
        copy_demo(project, data)
    assert _snapshot(root) == before


def test_typed_values_exist_in_every_language():
    assert set(TYPED) == set(LANGS)
    for lang, typed in TYPED.items():
        label, yes, no = typed["question"]
        assert label and yes and no and yes != no, lang


@pytest.mark.parametrize("code", ["en", "es-ES", "fr", "pt-BR"])
def test_the_run_button_pattern_reads_every_language(code):
    """``phases.prepare_labels`` matches the Harmonize … pending button in each catalogue."""
    msgs = REPO / "deforestation_alerts" / "message" / code / "steps.json"
    forms = json.loads(msgs.read_text(encoding="utf-8"))["steps"]["harmonize"]["run_all"]
    prepare = json.loads(msgs.read_text(encoding="utf-8"))["steps"]["prepare"]
    labels = ph.prepare_labels(forms.values(), "Ready", ["Queued"], prepare["footer_stop"])
    for form in forms.values():
        assert labels["run"].match(form.replace("{n}", "12"))
        assert labels["run"].match(form.replace("{n}", "0"))
    assert not labels["run"].match(prepare["footer_stop"])
    assert labels["stop"].match(prepare["footer_stop"])
