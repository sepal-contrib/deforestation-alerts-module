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
