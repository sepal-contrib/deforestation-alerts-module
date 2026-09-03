"""StageHost: the app-owned fixed overlay that hosts the Analysis two-map stage.

ipyleaflet only recomputes its canvas on a window resize, so a map first shown
inside a previously hidden container renders grey tiles; the template must
nudge the window when it becomes visible (same trick MapApp.vue uses)."""

import re

import ipyvuetify as v


def _script(tpl: str) -> str:
    """The <script> body of a runtime .vue template."""
    match = re.search(r"<script>(.*?)</script>", tpl, re.S)
    assert match, tpl
    return match.group(1)


def test_visible_trait_round_trips_and_hosts_content():
    from gui.widget.stage_host import StageHost

    stage = v.Html(tag="div", children=["stage"])
    host = StageHost(content=[stage])
    assert host.visible is False
    host.visible = True
    assert host.visible is True
    assert host.content == [stage]


def test_template_hides_with_v_show_and_nudges_leaflet_on_show():
    from gui.widget.stage_host import StageHost

    tpl = StageHost().template
    assert 'v-show="visible"' in tpl
    assert 'class="defo-stage"' in tpl
    # the ``content`` trait is what the stage div actually renders
    assert re.search(
        r'<jupyter-widget v-for="\(\w+, ?\w+\) in content"[^>]*:widget="\w+"', tpl
    ), tpl

    script = _script(tpl)
    # ipyvue evaluates the script with ``new Function("module", "exports", src)``;
    # ``export default`` is a SyntaxError there and only works through ipyvue's
    # explicitly-deprecated fallback, which warns on every mount.
    assert "module.exports" in script
    assert "export default" not in script

    # The nudge must hang off the *visible* watcher and fire only on the true
    # edge: watching any other trait leaves a first-shown map grey, which is the
    # whole reason this widget exists. Bind the pieces together rather than
    # checking that each token appears somewhere in the template.
    nudge = re.search(
        r"watch:\s*\{\s*visible\((\w+)\)\s*\{\s*if \(\1\)\s*\{(.*?)\n\s*\}",
        script,
        re.S,
    )
    assert nudge, script
    assert "$nextTick" in nudge.group(2)
    assert "new Event('resize')" in nudge.group(2)
