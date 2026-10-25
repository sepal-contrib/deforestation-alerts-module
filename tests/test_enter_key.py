import pytest

from gui.widget import enter_key


def test_the_default_class_is_the_apps_own():
    assert enter_key.ENTER_DEFAULT == "da-enter-default"
    assert 'querySelectorAll("button.da-enter-default")' in enter_key._TEMPLATE
    assert "sr-" not in enter_key._TEMPLATE


def test_the_template_is_only_module_exports():
    # ipyvue's legacy loader evaluates only the object literal: nothing before it.
    script = enter_key._TEMPLATE.split("<script>")[1].strip()
    assert script.startswith("module.exports = {")


def test_enter_in_a_date_field_presses_the_default_button():
    # HTML implicit submission covers date inputs too (the period modal's fields).
    assert '"date"' in enter_key._TEMPLATE.split("enterIsFree")[2]


def test_the_barrier_is_echoed():
    widget = enter_key.EnterKeyListenerWidget()
    sent = []
    widget.send = sent.append
    widget.vue_enter_barrier(7)
    assert sent == [{"method": "enter_barrier_passed", "args": [7]}]


def test_the_echo_clicks_only_a_button_still_marked_default():
    # A second Enter within one round-trip finds the button before its class
    # goes (a SubmitTaskButton turning into Cancel); its echo arrives after the
    # removal, so the echo handler must check the class again before clicking,
    # or Enter would cancel the check the first Enter started.
    handler = enter_key._TEMPLATE.split("jupyter_enter_barrier_passed(token) {")[1]
    handler = handler.split("\n    },\n")[0]
    check = 'button.classList.contains("da-enter-default")'
    assert check in handler and "button.click()" in handler
    assert handler.index(check) < handler.index("button.click()")


_PAGE = """<!doctype html><html><body>
<div id="overlay" vuetify-overlay class="v-application">
  <div class="v-dialog__content v-dialog__content--active" style="z-index:202" tabindex="0">
    <div class="v-select"><input id="select" type="text"></div>
    <input id="end" type="date" value="2025-07-31">
    <div class="da-enter-own"><input id="own" type="number"></div>
    <button class="da-enter-default" onclick="window.clicks++">Add period</button>
  </div>
</div></body></html>"""

_MOUNT = """(source) => {
    window.clicks = 0;
    // ipyvuetify's overlay div (jupyter-vuetify's createDivs): every keydown
    // that bubbles out of a dialog stops there, before document.
    document.getElementById('overlay').addEventListener('keydown', e => e.stopPropagation());
    const module = {exports: {}};
    new Function('module', source)(module);
    const vm = {};
    for (const [k, f] of Object.entries(module.exports.methods)) vm[k] = f.bind(vm);
    // the kernel's echo, at once
    vm.enter_barrier = token => setTimeout(() => vm.jupyter_enter_barrier_passed(token), 0);
    module.exports.mounted.call(vm);
}"""


def _enter_in(element_id):
    sync_api = pytest.importorskip("playwright.sync_api")
    script = enter_key._TEMPLATE.split("<script>")[1].split("</script>")[0]
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(_PAGE)
            page.evaluate(_MOUNT, script)
            page.focus(f"#{element_id}")
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            clicks = page.evaluate("() => window.clicks")
            browser.close()
    except sync_api.Error as exc:  # no browser installed here
        pytest.skip(f"no headless Chromium: {exc}")
    return clicks


def test_enter_reaches_the_listener_through_ipyvuetifys_overlay():
    """ipyvuetify stops every keydown on its overlay div, where the dialogs
    live, so a listener on document's bubble phase never hears Enter (found by
    the parity walk: Enter in the period modal's End field did nothing)."""
    assert _enter_in("end") == 1


def test_enter_in_a_select_is_still_the_selects():
    assert _enter_in("select") == 0


def test_enter_in_a_field_that_owns_it_presses_nothing():
    assert enter_key.ENTER_OWN == "da-enter-own"
    assert _enter_in("own") == 0
