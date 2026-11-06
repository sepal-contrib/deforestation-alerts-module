import json

from tools.i18n import edit as edit_mod


def test_put_and_drop_write_every_locale(tmp_path, monkeypatch):
    for loc in edit_mod.LOCALES:
        (tmp_path / loc).mkdir()
        (tmp_path / loc / "steps.json").write_text(json.dumps({"steps": {"a": {"old": "x", "keep": "k"}}}))
    monkeypatch.setattr(edit_mod, "MESSAGES", tmp_path)
    edit_mod.edit("steps", put=[("steps.a.new", "New", "Nuevo", "Nouveau", "Novo"), ("steps.b.c", "C", "C es", "C fr", "C pt")],
                  drop=["steps.a.old", "steps.missing.key"])
    es = json.loads((tmp_path / "es-ES" / "steps.json").read_text(encoding="utf-8"))
    assert es == {"steps": {"a": {"keep": "k", "new": "Nuevo"}, "b": {"c": "C es"}}}
    text = (tmp_path / "fr" / "steps.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and '  "steps": {' in text
