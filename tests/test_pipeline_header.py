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


def _strings(widget):
    if isinstance(widget, str):
        return [widget]
    out = []
    for child in getattr(widget, "children", None) or []:
        out += _strings(child)
    value = getattr(widget, "value", None)
    return out + ([value] if isinstance(value, str) else [])


def _attr(rc, cls, attr):
    return {w.attributes[attr]: w for w in rc.find(cls).widgets if attr in (w.attributes or {})}


def _rows(rc):
    import ipyvuetify as v

    return _attr(rc, v.ListItem, "data-step-key")


def _levels(rc):
    import ipyvuetify as v

    return _attr(rc, v.Subheader, "data-level-key")


def _footer(rc):
    import ipyvuetify as v

    return _attr(rc, v.Btn, "data-nav")


def test_menu_lists_every_step_under_its_level():
    from gui.store.workflow_steps import STEPS

    for key in ("integrate", "evaluators"):
        box, rc, _ = _render(key, _project(n_alerts=3))
        assert list(_rows(rc)) == [s.key for s in STEPS]
        assert list(_levels(rc)) == ["prepare", "review"]
        rc.close()


def test_menu_rows_mark_the_current_and_the_locked_step():
    from gui.i18n import t

    box, rc, seen = _render("aoi", _project(aoi_name="Acre"))
    rows = _rows(rc)
    assert rows["aoi"].attributes["aria-current"] == "step"
    assert rows["period"].attributes["aria-current"] == "false"
    # Review is locked without a set: its rows say so, with the reason
    assert rows["evaluators"].attributes["aria-disabled"] == "true" and rows["evaluators"].disabled
    assert "mdi-lock-outline" in _strings(rows["evaluators"])
    assert t("workflow.lock_review") in rows["evaluators"].attributes["title"]
    rows["evaluators"].fire_event("click", None)
    assert seen == []
    rows["period"].fire_event("click", None)
    assert seen == [1]
    rc.close()


def test_a_row_of_the_other_level_navigates_there():
    from gui.store.workflow_steps import step_index

    box, rc, seen = _render("aoi", _project(aoi_name="Acre", n_alerts=3))
    _rows(rc)["evaluators"].fire_event("click", None)
    assert seen == [step_index("evaluators")]
    rc.close()


def test_level_heading_ticked_once_its_last_required_step_has_outputs(monkeypatch):
    from gui.store import workflow_steps

    # A solo review gives Evaluation, Review's last required step, its
    # output. Integrate is locked in this fixture (no ready sources).
    p = _project(n_alerts=3)
    p.multi_evaluator = False
    box, rc, _ = _render("layers", p)
    levels = _levels(rc)
    assert "".join(_strings(levels["review"])).startswith("✓")
    assert "✓" not in "".join(_strings(levels["prepare"]))
    rc.close()
    monkeypatch.setattr(workflow_steps, "_ready", lambda p: ["GLAD-L"])  # Integrate unlocked
    box, rc, _ = _render("layers", _project(n_alerts=3))
    assert "".join(_strings(_levels(rc)["prepare"])).startswith("✓")
    rc.close()


def test_dots_tell_the_step_state():
    from gui.store.workflow_steps import STEPS, StepStatus, step_index
    from gui.widget.pipeline_header import dot_style

    p = _project(n_alerts=3)
    integrate, layers = STEPS[step_index("integrate")], STEPS[step_index("layers")]
    assert "background: #123456" in dot_style(integrate, StepStatus.HAS_OUTPUTS, p, "#123456")
    empty = dot_style(integrate, StepStatus.EMPTY, p, "#123456")
    assert "background" not in empty and "solid rgba(128" in empty
    # an optional step is dashed, hollow while it made nothing of its own
    # (passed-but-empty is HAS_OUTPUTS)
    opt = dot_style(layers, StepStatus.HAS_OUTPUTS, p, "#123456")
    assert "dashed #123456" in opt and "background" not in opt


def test_footer_back_and_next_cross_levels():
    from gui.store.workflow_steps import STEPS, step_index
    from gui.i18n import t

    box, rc, seen = _render("integrate", _project(n_alerts=3))
    nxt = _footer(rc)["next"]
    assert nxt.attributes["title"] == t(STEPS[step_index("layers")].label_key)
    nxt.fire_event("click", None)
    assert seen == [step_index("layers")]
    rc.close()
    # Back crosses to Prepare: its nearest unlocked step (the fixture has no
    # ready sources, so Integrate itself is locked and Back lands on AOI).
    box, rc, seen = _render("layers", _project(n_alerts=3))
    _footer(rc)["back"].fire_event("click", None)
    assert seen == [step_index("aoi")]
    rc.close()


def test_footer_has_no_back_on_the_first_step_and_no_next_on_the_last():
    box, rc, _ = _render("aoi", _project(aoi_name="Acre", n_alerts=3))
    assert list(_footer(rc)) == ["next"]
    rc.close()
    box, rc, _ = _render("evaluators", _project(n_alerts=3))
    assert list(_footer(rc)) == ["back"]
    rc.close()


def test_footer_labels_stay_fixed_whatever_the_step():
    from gui.i18n import t

    for key in ("period", "integrate", "layers"):
        box, rc, _ = _render(key, _project(aoi_name="Acre", n_alerts=3))
        f = _footer(rc)
        assert t("workflow.back") in _strings(f["back"]) and t("workflow.next") in _strings(f["next"])
        rc.close()


def _nav_button(rc, icon):
    import ipyvuetify as v

    (btn,) = [w for w in rc.find(v.Btn).widgets
              if any(isinstance(c, v.Icon) and c.children == [icon] for c in w.children)]
    return btn


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


def test_step_badge_counts_all_steps():
    from gui.i18n import t
    from gui.store.workflow_steps import STEPS

    for n, key in ((1, "aoi"), (5, "integrate"), (6, "layers"), (8, "evaluators")):
        box, rc, _ = _render(key, _project(n_alerts=3))
        assert _badge(rc) == t("workflow.step_badge", n=n, total=len(STEPS))
        rc.close()


def test_activator_names_the_current_step():
    import ipyvuetify as v

    box, rc, _ = _render("layers", _project(n_alerts=3))
    (wrap,) = [w for w in rc.find(v.Html).widgets if w.class_ == "sr-step-jump-wrap"]
    assert wrap.children[0].activator[0].attributes["data-active-step"] == "layers"
    rc.close()


def _title_strings(rc):
    import ipyvuetify as v

    (wrap,) = [w for w in rc.find(v.Html).widgets if w.class_ == "sr-step-jump-wrap"]
    return _strings(wrap.children[0].activator[0])


def test_an_empty_optional_step_shows_no_count_chip():
    from gui.i18n import t

    box, rc, _ = _render("layers", _project(n_alerts=3))
    assert _title_strings(rc) == [t("workflow.step_badge", n=6, total=8), t("app.drawer_item.layers"), "mdi-menu-down"]
    rc.close()


def test_an_optional_step_with_outputs_shows_its_count():
    from deforestation_alerts.project.models import GeeVisLayer
    from gui.i18n import t

    p = _project(n_alerts=3)
    p.vis_layers = {"gee": GeeVisLayer(name="gee", asset="users/x/img")}
    box, rc, _ = _render("layers", p)
    assert _badge(rc) == t("workflow.step_badge", n=6, total=8)
    assert t("workflow.count_layers", count=1) in _title_strings(rc)
    rc.close()


def test_one_header_render_plans_the_active_period_once(monkeypatch):
    from datetime import date

    from deforestation_alerts.engine.fingerprint import Status
    from deforestation_alerts.engine.plan import PrepareRow
    from deforestation_alerts.project.models import GridSpec, Period
    from gui.store import workflow_steps
    from gui.store.workflow_steps import step_index
    from gui.widget.pipeline_header import PipelineHeader

    calls = []

    def plan(p, pdir, period):
        calls.append(id(p))
        return [PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT)]  # downloaded and ready

    monkeypatch.setattr(workflow_steps, "plan_prepare", plan)
    p = _project(aoi_name="Acre")
    period = Period(id=JAN, start=date(2026, 1, 1), end=date(2026, 1, 31), sources=["GLAD-L"])
    p.periods = {JAN: period}
    p.active_period = JAN
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=1, height=1)
    project = _reactive(p)
    box, rc = reacton.render(PipelineHeader(active_step=step_index("harmonize"), on_navigate=lambda i: None,
                                            project=project), handle_error=False)
    assert len(calls) == 1
    project.set(p.model_copy())  # a change publishes a fresh copy: planned again, once
    assert len(calls) == 2 and calls[0] != calls[1]
    rc.close()


def test_unified_activator_and_testable_segments():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "workflow.step_badge" in src
    assert "mdi-menu-down" in src
    assert "sr-step-jump:hover" in src
    assert 'class_="sr-step-jump"' in src
    assert '"data-step-key"' in src  # the driver clicks menu rows by key
    assert '"data-active-step"' in src  # and reads the current step off the title
