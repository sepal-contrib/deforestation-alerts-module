"""PipelineHeader: badge text logic, render smoke, and codebase-gotcha
contracts (no rv.Btn(on_click), anchored dropdown, reactives-as-props).
Ported from the spatial_risk scaffold; gates read the Project."""

import inspect
from datetime import datetime

import reacton
import solara

from deforestation_alerts.project.models import AlertSet, AlertSetParams, AoiRecord, Project

JAN = "2026-01-01_2026-01-31"


def _project(aoi_name=None, n_reviewed=0, n_alerts=0):
    p = Project(name="demo", created_at=datetime.now(), updated_at=datetime.now())
    if aoi_name is not None:
        p.aoi = AoiRecord(spec={}, name=aoi_name, method="ADMIN1", digest="2317", bounds=(0, 0, 1, 1))
    if n_alerts or n_reviewed:
        p.alert_sets["x"] = AlertSet(
            id="x", period_id=JAN, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
            params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
            footprint_rel_path="f", n_alerts=n_alerts, n_reviewed=n_reviewed,
            fingerprint="f", created_at=datetime.now(),
        )
        p.active_set = "x"
    return p


def _reactive(p):
    return solara.reactive(p, equals=lambda a, b: a is b)


def test_count_text_pluralizes_and_falls_back():
    from gui.store.workflow_steps import STEPS, step_index
    from gui.widget.pipeline_header import count_text

    aoi_spec, alerts_spec, integrate = STEPS[0], STEPS[1], STEPS[step_index("integrate")]
    assert count_text(integrate, _project(n_alerts=5)) == "5 clusters"
    assert count_text(integrate, _project()) == "nothing yet"
    # AOI step: the badge is the AOI name (a string count).
    assert count_text(aoi_spec, _project(aoi_name="Acre")) == "Acre"
    assert count_text(aoi_spec, _project()) == "nothing yet"
    # Alerts step: count of configured sources (none in this fixture).
    assert count_text(alerts_spec, _project(aoi_name="Acre")) == "nothing yet"


def test_render_smoke_empty_session():
    from gui.widget.pipeline_header import PipelineHeader

    project = _reactive(None)
    box, rc = reacton.render(
        PipelineHeader(
            active_step=0, on_navigate=lambda i: None,
            project=project,
        ),
        handle_error=False,
    )
    rc.close()


def test_render_smoke_loaded_project():
    from gui.widget.pipeline_header import PipelineHeader

    project = _reactive(_project(aoi_name="Acre", n_alerts=3, n_reviewed=1))
    box, rc = reacton.render(
        PipelineHeader(
            active_step=5, on_navigate=lambda i: None,
            project=project,
        ),
        handle_error=False,
    )
    rc.close()


def test_jump_menu_is_a_dropdown_not_a_modal():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "solara.lab.Menu(" in src
    assert "rv.Dialog(" not in src
    # use_activator_width=False sends min-width="auto" to v-menu; Vuetify's
    # off-screen guard then computes NaN and stops clamping.
    assert "use_activator_width=False" not in src


def test_no_dead_click_patterns():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "rv.Btn(" not in src
    assert "rv.use_event(" in src
    # Reads the reactive inside the component (prop-equality bailout).
    assert "project.value" in src


def _render(active_key, p):
    from gui.store.workflow_steps import step_index
    from gui.widget.pipeline_header import PipelineHeader

    seen = []
    box, rc = reacton.render(
        PipelineHeader(active_step=step_index(active_key), on_navigate=seen.append, project=_reactive(p)),
        handle_error=False,
    )
    return box, rc, seen


def _htmls(rc, attr):
    import ipyvuetify as v

    return [w for w in rc.find(v.Html).widgets if attr in (w.attributes or {})]


def _strings(widget):
    if isinstance(widget, str):
        return [widget]
    out = []
    for child in getattr(widget, "children", None) or []:
        out += _strings(child)
    value = getattr(widget, "value", None)
    return out + ([value] if isinstance(value, str) else [])


def test_strip_shows_only_the_active_level():
    from gui.store.workflow_steps import steps_of, STEPS

    box, rc, _ = _render("integrate", _project(n_alerts=3))
    keys = [w.attributes["data-step-key"] for w in _htmls(rc, "data-step-key")]
    assert keys == [STEPS[i].key for i in steps_of("prepare")] and len(keys) == 5
    rc.close()
    box, rc, _ = _render("evaluators", _project(n_alerts=3))
    assert [w.attributes["data-step-key"] for w in _htmls(rc, "data-step-key")] == [
        "layers", "questionnaire", "evaluators"]
    rc.close()


def test_level_tabs_with_lock_reason_and_tick():
    from gui.i18n import t

    box, rc, _ = _render("aoi", _project(aoi_name="Acre"))
    tabs = {w.attributes["data-level-key"]: w for w in _htmls(rc, "data-level-key")}
    assert list(tabs) == ["prepare", "review"]
    text = "".join(_strings(box))
    assert t("workflow.level_prepare") in text and t("workflow.level_review") in text
    assert tabs["review"].attributes["title"] == t("workflow.lock_review")
    rc.close()
    # A solo review gives Evaluation, Review's last required step, its
    # output: the Review tab is ticked. Integrate is locked in this fixture (no
    # ready sources), so Prepare keeps its number.
    p = _project(n_alerts=3)
    p.multi_evaluator = False
    box, rc, _ = _render("layers", p)
    tabs = {w.attributes["data-level-key"]: w for w in _htmls(rc, "data-level-key")}
    assert "".join(_strings(tabs["review"])).startswith("✓")
    assert "".join(_strings(tabs["prepare"])).startswith("1")
    rc.close()


def test_review_tab_locked_without_set():
    from gui.i18n import t

    box, rc, seen = _render("aoi", _project(aoi_name="Acre"))
    review = {w.attributes["data-level-key"]: w for w in _htmls(rc, "data-level-key")}["review"]
    review.fire_event("click", None)
    assert seen == []
    assert t("workflow.lock_review") in "".join(_strings(box))
    rc.close()


def test_review_tab_opens_its_first_unlocked_step():
    from gui.store.workflow_steps import step_index

    box, rc, seen = _render("integrate", _project(n_alerts=3))
    review = {w.attributes["data-level-key"]: w for w in _htmls(rc, "data-level-key")}["review"]
    review.fire_event("click", None)
    assert seen == [step_index("layers")]
    rc.close()


def _nav_button(rc, icon):
    import ipyvuetify as v

    (btn,) = [w for w in rc.find(v.Btn).widgets
              if any(isinstance(c, v.Icon) and c.children == [icon] for c in w.children)]
    return btn


def test_next_from_integrate_opens_review():
    from gui.store.workflow_steps import step_index

    box, rc, seen = _render("integrate", _project(n_alerts=3))
    _nav_button(rc, "mdi-chevron-right").fire_event("click", None)
    assert seen == [step_index("layers")]
    rc.close()
    # Back crosses to Prepare: its nearest unlocked step (the fixture has no
    # ready sources, so Integrate itself is locked and Back lands on AOI).
    box, rc, seen = _render("layers", _project(n_alerts=3))
    _nav_button(rc, "mdi-chevron-left").fire_event("click", None)
    assert seen == [step_index("aoi")]
    rc.close()


def test_instruction_line_and_info_follow_the_step():
    import ipyvuetify as v
    from gui.i18n import t

    for key in ("integrate", "aoi"):
        box, rc, _ = _render(key, _project(n_alerts=3))
        assert t(f"steps.{key}.instruction") in _strings(box)
        info = _nav_button(rc, "mdi-information-outline")
        info.fire_event("click", None)
        dialog = "".join(_strings(rc.find(v.Dialog).widget))
        assert t(f"steps.{key}.info_header") in dialog
        rc.close()


def _badge(rc):
    import ipyvuetify as v

    # The badge sits in the jump menu's activator slot, which rc.find skips.
    (wrap,) = [w for w in rc.find(v.Html).widgets if w.class_ == "sr-step-jump-wrap"]
    (badge,) = [w for w in wrap.children[0].activator[0].children if getattr(w, "class_", "") == "defo-step-badge"]
    return badge.children[0]


def test_optional_badge():
    from gui.i18n import t

    box, rc, _ = _render("layers", _project(n_alerts=3))
    assert _badge(rc) == t("workflow.optional")
    rc.close()
    box, rc, _ = _render("evaluators", _project(n_alerts=3))
    assert _badge(rc) == t("workflow.step_badge", n=3, total=3)
    rc.close()
    box, rc, _ = _render("integrate", _project(n_alerts=3))
    assert _badge(rc) == t("workflow.step_badge", n=5, total=5)
    rc.close()


def test_optional_segments_are_dashed():
    import ipyvuetify as v

    box, rc, _ = _render("evaluators", _project(n_alerts=3))
    cells = {w.attributes["data-step-key"]: w for w in _htmls(rc, "data-step-key")}
    bar = {k: c.children[0].style_ for k, c in cells.items() if isinstance(c.children[0], v.Html)}
    assert "dashed" in bar["layers"] and "transparent" in bar["layers"]
    assert "dashed" not in bar["evaluators"]
    rc.close()


def test_unified_activator_and_testable_segments():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "workflow.step_badge" in src
    assert "mdi-menu-down" in src
    assert "sr-step-jump:hover" in src
    assert 'class_="sr-step-jump"' in src
    assert '"data-step-key"' in src  # the driver clicks segments by key
