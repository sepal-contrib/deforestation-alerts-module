import reacton
import solara

from gui.widget import dialog_nudge


def test_nudge_class_alternates_after_the_first_click():
    assert dialog_nudge.nudge_class(0) == ""
    assert [dialog_nudge.nudge_class(n) for n in (1, 2, 3)] == [
        "da-dialog-nudge-a", "da-dialog-nudge-b", "da-dialog-nudge-a"]
    for name in ("da-dialog-nudge-a", "da-dialog-nudge-b"):
        assert f"@keyframes {name}" in dialog_nudge.NUDGE_CSS and f".{name} {{" in dialog_nudge.NUDGE_CSS


def test_the_hook_counts_clicks_and_resets_when_the_dialog_opens_or_closes():
    open_ = solara.reactive(True)
    seen = {}

    @solara.component
    def Probe():
        nudge, on_click_outside = dialog_nudge.use_outside_click_nudge(open_.value)
        seen["nudge"], seen["click"] = nudge, on_click_outside

    box, rc = reacton.render(Probe(), handle_error=False)
    try:
        assert seen["nudge"] == ""
        seen["click"]()
        assert seen["nudge"] == "da-dialog-nudge-a"
        seen["click"]()
        assert seen["nudge"] == "da-dialog-nudge-b"
        open_.set(False)
        assert seen["nudge"] == ""
    finally:
        rc.close()
