"""PipelineHeader: badge text logic, render smoke, and codebase-gotcha
contracts (no rv.Btn(on_click), anchored dropdown, reactives-as-props).
Ported from the spatial_risk scaffold; gate inputs here are
(alerts_gdf, aoi_name)."""

import inspect

import pandas as pd
import reacton
import solara


def _gdf(n, reviewed=0):
    status = ["Confirmed"] * reviewed + ["Not reviewed"] * (n - reviewed)
    return pd.DataFrame({"status": status})


def test_count_text_pluralizes_and_falls_back():
    from gui.store.workflow_steps import STEPS
    from gui.widget.pipeline_header import count_text

    filter_spec, analysis = STEPS[1], STEPS[2]
    assert count_text(filter_spec, _gdf(2), "Acre") == "2 alerts"
    assert count_text(filter_spec, _gdf(1), "Acre") == "1 alert"
    assert count_text(filter_spec, _gdf(0), "Acre") == "nothing yet"
    assert count_text(analysis, _gdf(5, reviewed=2), "Acre") == "2 reviewed"
    # AOI step: the badge is the AOI name (a string count).
    assert count_text(STEPS[0], None, "Acre") == "Acre"
    assert count_text(STEPS[0], None, None) == "nothing yet"


def test_render_smoke_empty_session():
    from gui.widget.pipeline_header import PipelineHeader

    gdf = solara.reactive(None, equals=lambda a, b: a is b)
    aoi = solara.reactive(None)
    box, rc = reacton.render(
        PipelineHeader(
            active_step=0, on_navigate=lambda i: None,
            alerts_gdf=gdf, aoi_name=aoi,
        ),
        handle_error=False,
    )
    rc.close()


def test_render_smoke_loaded_alerts():
    from gui.widget.pipeline_header import PipelineHeader

    gdf = solara.reactive(_gdf(3, reviewed=1), equals=lambda a, b: a is b)
    aoi = solara.reactive("Acre")
    box, rc = reacton.render(
        PipelineHeader(
            active_step=2, on_navigate=lambda i: None,
            alerts_gdf=gdf, aoi_name=aoi,
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
    # Reads reactives inside the component (prop-equality bailout).
    assert "alerts_gdf.value" in src and "aoi_name.value" in src


def test_unified_activator_and_testable_segments():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "workflow.step_badge" in src
    assert "mdi-menu-down" in src
    assert "sr-step-jump:hover" in src
    assert 'class_="sr-step-jump"' in src
    assert '"data-step-key"' in src  # the driver clicks segments by key
