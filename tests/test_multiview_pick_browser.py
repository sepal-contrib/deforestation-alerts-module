"""The pick plugin through the widget (committed bundle, fake model): a map
click writes a tagged ``pick_event``, nothing is picked in edit mode, and the
plugin needs draw."""

from tests._multiview_page import mount, mv_browser, mv_eval, mv_page, py, writes  # noqa: F401


def _mount(page, **state):
    mount(page, host_style="width:840px;height:420px",
          view={"center": [-1.0, -60.0], "zoom": 13, "n": 1, "origin": "host"}, **state)
    page.wait_for_timeout(250)


def _click_centre(page, view_id):
    x, y = mv_eval(page, """(mv, id) => { const view = mv.views.find(v => v.id === id);
        const r = view.map.getContainer().getBoundingClientRect();
        return [r.left + r.width / 2, r.top + r.height / 2]; }""", view_id)
    page.mouse.click(x, y)
    page.wait_for_timeout(250)


def test_a_click_writes_the_pick_event_with_tag_and_zoom(mv_page):
    _mount(mv_page)
    py(mv_page, "pick_tag", "7:1")
    _click_centre(mv_page, "after")
    events = writes(mv_page, "pick_event")
    assert len(events) == 1
    event = events[-1]
    assert event["tag"] == "7:1" and event["view"] == "after" and event["zoom"] == 13
    assert event["seq"] == 1
    assert abs(event["lat"] - -1.0) < 0.01 and abs(event["lng"] - -60.0) < 0.01
    _click_centre(mv_page, "before")
    second = writes(mv_page, "pick_event")[-1]
    assert second["seq"] == 2 and second["view"] == "before"


def test_nothing_is_picked_in_edit_mode(mv_page):
    _mount(mv_page, draw_edit_mode=True)
    _click_centre(mv_page, "after")
    assert writes(mv_page, "pick_event") == []


def test_pick_requires_draw(mv_page):
    mount(mv_page, plugins=["pick"])
    assert mv_page.locator("#host .mv-error").inner_text() == "pick: pick requires draw, which is not installed yet"
