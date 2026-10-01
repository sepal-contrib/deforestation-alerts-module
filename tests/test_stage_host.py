"""StageHost: the app-owned fixed overlay that hosts the Analysis two-map stage.

Leaflet only re-measures a map on a window resize, and a map hidden at that
moment measures 0x0 and keeps drawing tiles for that size once shown again. The
template must nudge the window when the stage shows (its own maps were hidden)
AND when it hides (the shell's shared map was hidden while it showed) — the
same trick MapApp.vue uses."""

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


def test_template_hides_with_v_show_and_nudges_leaflet_on_both_edges():
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

    # The nudge must hang off the *visible* watcher (watching any other trait
    # leaves a first-shown stage map grey) and fire on BOTH edges: gated to the
    # true edge, leaving Analysis left the shared map drawing tiles for the 0x0
    # size leaflet measured while it was hidden — only its top-left corner
    # filled in until a window resize. Bind the pieces together rather than
    # checking that each token appears somewhere in the template.
    watcher = re.search(r"watch:\s*\{\s*visible\(\w*\)\s*\{(.*?)\n\s*\},\s*\},", script, re.S)
    assert watcher, script
    body = watcher.group(1)
    assert "$nextTick" in body and "window.dispatchEvent(new Event('resize'))" in body, body
    assert "if" not in re.findall(r"\b\w+\b", body), "the nudge must not depend on the edge: " + body
