import reacton
import solara

from gui.widget.default_name import use_default_name


def _render(ref):
    seen = {}

    @solara.component
    def Probe():
        name, set_name = solara.use_state("")
        seen["name"], seen["set"] = name, set_name
        use_default_name(ref.value, name, set_name, lambda r: r.upper())
        solara.Text(name)

    box, rc = reacton.render(Probe(), handle_error=False)
    return seen, rc


def test_the_name_follows_the_ref_until_the_user_types():
    ref = solara.reactive("")
    seen, rc = _render(ref)
    assert seen["name"] == ""  # an empty ref leaves the name alone
    ref.value = "a"
    assert seen["name"] == "A"
    ref.value = "b"
    assert seen["name"] == "B"
    rc.close()


def test_a_name_typed_by_hand_is_kept():
    ref = solara.reactive("a")
    seen, rc = _render(ref)
    assert seen["name"] == "A"
    seen["set"]("mine")
    ref.value = "b"
    assert seen["name"] == "mine"
    rc.close()


def test_an_emptied_ref_keeps_the_name():
    ref = solara.reactive("a")
    seen, rc = _render(ref)
    ref.value = ""
    assert seen["name"] == "A"
    rc.close()
