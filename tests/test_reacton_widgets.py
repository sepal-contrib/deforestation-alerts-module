"""find_rendered_widget: the fallback the shell uses to resolve the MapApp widget.

A child that sets a reactive while rendering re-renders the parent before the
parent's mount effect runs. reacton then keeps the widget keyed by the first
pass's element, so ``solara.get_widget`` on the element the effect holds
raises KeyError (what broke navigation once the LogConsole was removed).
"""

import ipyvuetify as v
import reacton
import solara

from gui.scripts.reacton_widgets import find_rendered_widget


def _render_shell():
    bump = solara.reactive(0)
    seen = {}

    @solara.component
    def Bumper():
        if bump.value == 0:
            bump.set(1)  # a reactive set during render: forces a second pass

    @solara.component
    def Shell():
        _ = bump.value  # re-render Shell when the child bumps
        el = v.Card.element(children=[v.Html.element(tag="div", children=["x"])])

        def resolve():
            try:
                seen["get_widget"] = solara.get_widget(el)
            except KeyError:
                seen["get_widget"] = None
            seen["fallback"] = find_rendered_widget(v.Card)

        solara.use_effect(resolve, [])
        Bumper()

    _, rc = reacton.render(Shell(), handle_error=False)
    return seen, rc


def test_fallback_finds_the_widget_get_widget_misses():
    seen, rc = _render_shell()
    try:
        assert seen["get_widget"] is None  # the reacton behaviour worked around
        assert isinstance(seen["fallback"], v.Card)
    finally:
        rc.close()


def test_returns_none_when_nothing_matches():
    found = {}

    @solara.component
    def Empty():
        solara.use_effect(lambda: found.setdefault("w", find_rendered_widget(v.Card)), [])
        solara.Text("nothing to find")

    _, rc = reacton.render(Empty(), handle_error=False)
    try:
        assert found["w"] is None
    finally:
        rc.close()
