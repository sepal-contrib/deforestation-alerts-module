"""Offline check of the walks' Analysis selectors against the built widget tree.

    micromamba run -n defo-alerts python -m tools.walk.check_selectors

The walks in this package are Playwright browser walks: they need a SEPAL
session and Earth Engine, they are not pytest-collected, and a green test suite
says nothing about them. Worse, ``phases.run_phases`` stops at the first
failing phase, so ONE selector that no longer resolves silently costs every
phase after it.

The commonest way for that to happen, since the Analysis panel became the
review console (spec 2026-10-03), is a widget that now lives inside a
COLLAPSED section body: *Questions*, *Refine polygon* and *Export this alert*
hide their bodies with ``display``, so nothing inside them is ``:visible`` until
``phases.open_section`` clicks the header. A phase that reaches in without
opening it first times out.

This script needs no browser: it builds ``analysis_tile.build_controls`` on
stand-in models (as tests/test_analysis_sections.py does), walks every widget,
and records for each piece of visible text whether it sits inside a section
body. ``WALK`` below then declares, per walk phase, the Analysis texts that
phase reaches for and which sections it opens first, and the script fails on
any text that is inside a section the phase does not open -- or that is not in
the tree at all. Extend ``WALK`` whenever a phase starts reaching for
something new in the panel.

What it does NOT check: CSS structure (``.v-select__slot``, ``.v-chip``),
anything outside the Analysis controls, and whether a phase runs at all. No
Playwright selector is evaluated and no browser is started -- the texts in
``WALK`` are the claim, checked against the built tree. It is a selector smoke
test, not a substitute for running a walk.

And the one limit worth naming, because it is the way the collapsed-section
bug this script exists to catch comes back: a phase that reaches for something
``WALK`` does not DECLARE is invisible here. Nothing reads the phase sources,
so a new ``page.get_by_text(...)`` inside a collapsed section passes this
script in silence and times out in the walk. The declaration is the contract:
extend ``WALK`` in the same change as the phase, or this script says nothing
about it. (Deriving the texts from the phase sources instead -- a backward
grep over ``tools/walk/*.py`` -- was weighed and deliberately left out: it is a
larger change than the check it would replace.)
"""

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

#: ``(phase, sections it opens first, the Analysis texts it reaches for)``.
#: A text is matched as a substring, so "Download" covers "Download alert data".
WALK = [
    ("phases.integrate_and_land (alert_facts)", (), ("Source", "Detected", "Status")),
    ("phases.review_and_save (verdict button)", (), ("True alert",)),
    ("phases.review_and_save (loss driver)", ("Questions",), ("Loss driver",)),
    ("phases.review_and_save (save_btn)", (), ("Verdict", "Save")),
    ("phases.review_and_save (download_btn)", ("Export this alert",), ("Download",)),
    ("phases.download_with_images (download_btn)", ("Export this alert",), ("Download",)),
    ("phases.open_section (the headers themselves)", (),
     ("Questions", "Refine polygon", "Export this alert")),
    ("phases.analysis_jump_field (after the # toggle)", (), ("Go",)),
    ("slice5.e_sets (the set selector)", (), ("Alert set",)),
    ("slice6.pick_template (report template field)", ("Export this alert",), ("Report template",)),
]

#: Texts the panel renders only once the project has sets / the row is revealed.
#: They are hidden in the stand-in panel below for a reason that is not a
#: collapsed section, so the section check does not apply to them.
HIDDEN_BY_OWN_STATE = {
    "Alert set": "the set selector hides itself while the project has no sets",
    "Go": "the jump row is revealed by block A's # toggle (analysis_jump_field clicks it)",
}


def _texts(root, hidden=False, out=None):
    """``{text: {"visible"|"hidden"}}`` for every string in ``root``'s tree.

    "hidden" means some ancestor (or the widget itself) is out of the layout:
    ``display: none`` (what a collapsed section body sets) or ``viz=False`` /
    the ``d-none`` class (what ``sw.SepalWidget.hide()`` sets instead -- it
    never touches ``style_``, so reading only that counted a hidden toolbar as
    visible and let a phase that reaches into one pass).
    """
    out = {} if out is None else out
    mine = (hidden
            or "display: none" in (getattr(root, "style_", "") or "")
            or getattr(root, "viz", True) is False
            or "d-none" in (getattr(root, "class_", "") or "").split())
    for attr in ("msg", "label", "tooltip"):
        value = getattr(root, attr, None)
        if isinstance(value, str) and value.strip():
            out.setdefault(value.strip(), set()).add("hidden" if mine else "visible")
    for child in getattr(root, "children", None) or []:
        if isinstance(child, str):
            if child.strip():
                out.setdefault(child.strip(), set()).add("hidden" if mine else "visible")
        else:
            _texts(child, mine, out)
    return out


def build_panel(tmp):
    """``(controls, sections)`` of a panel built on stand-in models."""
    import ipyvuetify as v

    from deforestation_alerts.project.models import Project
    from gui.store.state_manager import AppState
    from gui.tile.analysis import analysis_tile
    from gui.tile.analysis.state import AnalysisState

    now = datetime.now()
    app_state = AppState()
    app_state.new_project_state(Project(name="d", created_at=now, updated_at=now))
    model = SimpleNamespace(recipe_folder_path=tmp, custom_report_template=None,
                            alert_set=None, remember_position=lambda *_: None)
    draw = SimpleNamespace(clear=lambda: None)
    controls = analysis_tile.build_controls(
        widgets=SimpleNamespace(draw_before=draw, draw_after=draw),
        app_state=app_state, analysis=AnalysisState(), aux_model=model,
        app_tile_model=model, id_field=v.TextField(v_model=None))
    return controls, analysis_tile.panel_sections(controls)


def main():
    tmp = tempfile.mkdtemp()
    os.environ.setdefault("DEFORESTATION_ALERTS_DATA_DIR", tmp)
    controls, sections = build_panel(tmp)

    from gui.i18n import t

    panel = _texts(controls)
    bodies = {"Questions": _texts(sections.questions.body_box),
              "Refine polygon": _texts(sections.refine.body_box),
              "Export this alert": _texts(sections.export.body_box)}
    # The report-template field is a Solara subtree, not raw widgets, so its
    # own catalogue strings are added by hand.
    for key in ("report_template_label", "report_template_hint"):
        text = t(f"analysis_tile.export_labels.{key}")
        panel.setdefault(text, set()).add("hidden")
        bodies["Export this alert"].setdefault(text, set()).add("hidden")

    problems = []
    print(f"{'phase':46} {'text':26} where")
    for phase, opens, wanted in WALK:
        for want in wanted:
            hits = [text for text in panel if want in text]
            if not hits:
                print(f"{phase:46} {want:26} !! NOT IN THE TREE")
                problems.append(f"{phase}: {want!r} matches nothing in the panel")
                continue
            for text in hits:
                inside = [name for name, body in bodies.items() if text in body]
                where = ", ".join(f"inside {name!r}" for name in inside) or \
                    f"panel ({'/'.join(sorted(panel[text]))})"
                print(f"{phase:46} {text[:26]:26} {where}")
                for name in inside:
                    if name not in opens:
                        problems.append(
                            f"{phase}: {text!r} is inside the collapsed {name!r} "
                            f"section -- the phase must call "
                            f"ph.open_section(page, {name!r}) first")
            # Hidden for a reason that is NOT a section: either the phase
            # reveals it itself (and says so here) or nobody knows why.
            for text in hits:
                in_a_section = any(text in body for body in bodies.values())
                if (not in_a_section and panel[text] == {"hidden"}
                        and want not in HIDDEN_BY_OWN_STATE):
                    problems.append(
                        f"{phase}: {text!r} is hidden and not inside a section -- "
                        f"record why in HIDDEN_BY_OWN_STATE, or fix the phase")

    print("\n--- every text inside a collapsed section body (needs open_section) ---")
    for name, body in bodies.items():
        print(f"  {name}:")
        for text in sorted(body):
            print(f"      {text[:68]!r}")

    if problems:
        print("\nFAIL")
        for problem in problems:
            print(f"  * {problem}")
        return 1
    print("\nOK: every Analysis text WALK declares is in the built tree, and "
          "every one inside a collapsed section is opened by its phase first.\n"
          "    No Playwright selector was evaluated, and a text no phase "
          "declares here was not checked at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
