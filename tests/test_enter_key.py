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
