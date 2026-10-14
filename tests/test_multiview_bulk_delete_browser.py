"""The trash button's bulk deletes (draw plugin), through the widget (committed
bundle, fake model), in three views: *Select area* deletes the shapes wholly
inside a dragged box and *Clear all* every shape, each only after its
confirmation's *Delete*, with one write; *Cancel*, Esc, *Finish*, leaving edit
mode and a host write all close a confirmation without deleting anything."""

import pytest

from tests._multiview_page import (  # noqa: F401
    mount,
    mv_browser,
    mv_eval,
    mv_page,
    py,
    writes,
)

VIEWS = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
D = 0.004  # half the big square's side in degrees: about 47 px at zoom 14


def _square(cx, cy, half):
    return [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half],
            [cx - half, cy - half]]


BIG = _square(-60.0, -1.0, D)
SMALL = _square(-60.0 + 2.4 * D, -1.0 + 2.4 * D, 0.3 * D)  # up and right of BIG, clear of it
FAR = _square(-60.0 - 2.4 * D, -1.0 - 2.4 * D, 0.3 * D)  # down and left of BIG
# A box over BIG's upper-right corner that holds SMALL whole.
BOX = ([-60.0 + 0.5 * D, -1.0 + 0.5 * D], [-60.0 + 3.0 * D, -1.0 + 3.0 * D])


def _env(gen, *rings):
    return {"fc": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [r]}} for r in rings]},
        "gen": gen, "seq": 0, "origin": "host"}


def _mount(page, rings=(BIG, SMALL, FAR)):
    mount(page, host_style="width:840px;height:420px", views=VIEWS, draw=_env(1, *rings), draw_edit_mode=True)
    page.wait_for_timeout(250)  # Geoman enables layers added in edit mode through a 100 ms throttle


def _view(view_id):
    return f'.mv-view[data-view="{view_id}"]'


def _rings(page, view_id):
    return mv_eval(page, """(mv, id) => { const out = []; const view = mv.views.find(v => v.id === id);
        view.map.eachLayer(l => { if (l._mvDrawId !== undefined) out.push(l.toGeoJSON(false).geometry.coordinates[0]); });
        return out; }""", view_id)


def _everywhere(page):
    a = _rings(page, "a")
    assert _rings(page, "b") == a and _rings(page, "c") == a
    return a


def _xy(page, view_id, lng, lat):
    return mv_eval(page, """(mv, [id, lng, lat]) => { const view = mv.views.find(v => v.id === id);
        const p = view.map.latLngToContainerPoint([lat, lng]); const r = view.map.getContainer().getBoundingClientRect();
        return [r.left + p.x, r.top + p.y]; }""", [view_id, lng, lat])


def _center(page, view_id):
    return mv_eval(page, """(mv, id) => { const c = mv.views.find(v => v.id === id).map.getCenter();
        return [c.lng, c.lat]; }""", view_id)


def _draws(page):
    return writes(page, "draw")


def _trash(page, view_id="a"):
    page.locator(f"{_view(view_id)} .leaflet-pm-icon-delete").click()


def _action(page, name, view_id="a"):
    return page.locator(f"{_view(view_id)} .leaflet-pm-action.action-{name}")


def _removal_on(page, view_id="a"):
    return mv_eval(page, "(mv, id) => mv.views.find(v => v.id === id).map.pm.globalRemovalModeEnabled()", view_id)


def _drag_box(page, view_id, frm, to, *, release=True):
    x, y = _xy(page, view_id, *frm)
    page.mouse.move(x, y)
    page.mouse.down()
    x, y = _xy(page, view_id, *to)
    page.mouse.move(x, y, steps=10)
    if release:
        page.mouse.up()
        page.wait_for_timeout(50)


def _doomed(page, view_id="a"):
    return page.locator(f"{_view(view_id)} .mv-draw-doomed").count()


def _confirm(page, view_id="a"):
    return page.locator(f"{_view(view_id)} .mv-bulk-confirm")


# --- the action strip -------------------------------------------------------------


def test_the_trash_offers_select_area_then_clear_all_then_finish(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    texts = mv_page.locator(f"{_view('a')} .leaflet-pm-icon-delete >> xpath=../.. "
                            ">> .leaflet-pm-action").all_inner_texts()
    assert texts == ["Select area", "Clear all", "Finish"]


def test_the_actions_follow_the_labels(mv_page):
    _mount(mv_page)
    py(mv_page, "labels", {"draw.selectArea": "Seleccionar área", "draw.clearAll": "Borrar todo"})
    _trash(mv_page)
    assert _action(mv_page, "mvSelectArea").inner_text() == "Seleccionar área"
    assert _action(mv_page, "mvClearAll").inner_text() == "Borrar todo"


# --- Clear all ----------------------------------------------------------------------


def test_clear_all_asks_first_and_deletes_every_shape_with_one_write(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvClearAll").click()
    confirm = _confirm(mv_page)
    assert confirm.locator(".mv-bulk-message").inner_text() == "Delete all 3 shapes?"
    assert _doomed(mv_page, "a") == 3 and _doomed(mv_page, "c") == 3
    assert _draws(mv_page) == [], "nothing is deleted before Delete"

    confirm.locator(".mv-bulk-delete").click()
    mv_page.wait_for_timeout(100)
    assert len(_draws(mv_page)) == 1
    assert _draws(mv_page)[0]["fc"]["features"] == []
    assert _everywhere(mv_page) == []
    assert _confirm(mv_page).count() == 0
    assert not _removal_on(mv_page), "Clear all ends removal mode"


def test_clear_all_cancel_keeps_everything_and_stays_in_removal_mode(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvClearAll").click()
    _confirm(mv_page).locator(".mv-bulk-cancel").click()
    assert _draws(mv_page) == []
    assert len(_everywhere(mv_page)) == 3
    assert _doomed(mv_page) == 0 and _confirm(mv_page).count() == 0
    assert _removal_on(mv_page)


def test_clear_all_on_an_empty_drawing_asks_nothing(mv_page):
    _mount(mv_page, rings=())
    _trash(mv_page)
    _action(mv_page, "mvClearAll").click()
    assert _confirm(mv_page).count() == 0
    assert _draws(mv_page) == []


def test_one_shape_reads_in_the_singular(mv_page):
    _mount(mv_page, rings=(BIG,))
    _trash(mv_page)
    _action(mv_page, "mvClearAll").click()
    assert _confirm(mv_page).locator(".mv-bulk-message").inner_text() == "Delete 1 shape?"


# --- Select area ----------------------------------------------------------------------


def test_select_area_deletes_only_the_shapes_wholly_inside_the_box(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    assert mv_page.locator(f"{_view('a')} .mv-draw-hint").inner_text() == \
        "Drag a box around the shapes to delete · Esc to cancel"
    center = _center(mv_page, "a")
    _drag_box(mv_page, "a", *BOX, release=False)
    assert _doomed(mv_page) == 1, "the shapes inside turn red while the box grows"
    mv_page.mouse.up()
    mv_page.wait_for_timeout(50)

    assert _center(mv_page, "a") == center, "the drag draws a box, it does not pan"
    confirm = _confirm(mv_page)
    assert confirm.locator(".mv-bulk-message").inner_text() == "Delete 1 shape?"
    assert mv_page.locator(f"{_view('a')} .mv-select-box").count() == 1
    assert _draws(mv_page) == []

    confirm.locator(".mv-bulk-delete").click()
    mv_page.wait_for_timeout(100)
    assert len(_draws(mv_page)) == 1
    assert _everywhere(mv_page) == [BIG, FAR]
    assert mv_page.locator(f"{_view('a')} .mv-select-box").count() == 0
    assert _removal_on(mv_page), "back in removal mode after a box"
    assert mv_page.locator(f"{_view('a')} .mv-draw-hint").inner_text() == "Right-click a vertex to delete it"


def test_the_confirmation_sits_beside_the_box_inside_the_view(mv_page):
    """Near the view's right edge there is no room to the right of the box:
    the confirmation moves to its left rather than over the shapes."""
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    _drag_box(mv_page, "a", *BOX)
    box = mv_page.locator(f"{_view('a')} .mv-select-box").bounding_box()
    confirm = _confirm(mv_page).bounding_box()
    view = mv_page.locator(_view("a")).bounding_box()
    overlap_x = min(box["x"] + box["width"], confirm["x"] + confirm["width"]) - max(box["x"], confirm["x"])
    overlap_y = min(box["y"] + box["height"], confirm["y"] + confirm["height"]) - max(box["y"], confirm["y"])
    assert overlap_x <= 0 or overlap_y <= 0, (box, confirm)
    assert view["x"] <= confirm["x"] and confirm["x"] + confirm["width"] <= view["x"] + view["width"]


def test_the_armed_action_is_highlighted(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    select = _action(mv_page, "mvSelectArea")

    def colour():
        # Off and unfocused: Geoman greys a hovered or focused action.
        mv_page.mouse.move(1, 1)
        return select.evaluate("el => (el.blur(), getComputedStyle(el).backgroundColor)")

    plain = colour()
    select.click()
    assert colour() == "rgb(25, 118, 210)" != plain
    mv_page.keyboard.press("Escape")
    assert colour() == plain


def test_a_box_dragged_from_inside_a_polygon_does_not_remove_it(mv_page):
    """Removal mode deletes a polygon on click; a box that starts and ends on
    one must not reach it."""
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    _drag_box(mv_page, "a", [-60.0 - 0.5 * D, -1.0 - 0.5 * D], [-60.0 + 0.5 * D, -1.0 + 0.5 * D])
    mv_page.wait_for_timeout(150)
    assert _draws(mv_page) == []
    assert len(_everywhere(mv_page)) == 3
    assert _confirm(mv_page).count() == 0, "nothing wholly inside: no confirmation"


def test_an_empty_box_asks_nothing_and_stays_armed(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    _drag_box(mv_page, "a", [-60.0 + 1.3 * D, -1.0 - 3.0 * D], [-60.0 + 3.0 * D, -1.0 - 1.5 * D])
    assert _confirm(mv_page).count() == 0
    assert mv_page.locator(f"{_view('a')} .mv-select-box").count() == 0
    _drag_box(mv_page, "a", *BOX)
    assert _confirm(mv_page).count() == 1, "still armed: the next box asks"


def test_select_area_cancel_keeps_the_shapes(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    _drag_box(mv_page, "a", *BOX)
    _confirm(mv_page).locator(".mv-bulk-cancel").click()
    assert _draws(mv_page) == []
    assert len(_everywhere(mv_page)) == 3
    assert _doomed(mv_page) == 0
    assert mv_page.locator(f"{_view('a')} .mv-select-box").count() == 0
    assert _removal_on(mv_page)


def test_a_click_on_the_map_while_asking_deletes_nothing(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    _drag_box(mv_page, "a", *BOX)
    x, y = _xy(mv_page, "a", -60.0, -1.0)  # on BIG, which removal mode would delete
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(150)
    assert _draws(mv_page) == []
    assert len(_everywhere(mv_page)) == 3
    assert _confirm(mv_page).count() == 1


@pytest.mark.parametrize("closer", ["escape", "finish", "edit mode off", "host write"])
def test_a_confirmation_closes_without_deleting(mv_page, closer):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    _drag_box(mv_page, "a", *BOX)
    assert _confirm(mv_page).count() == 1
    if closer == "escape":
        mv_page.keyboard.press("Escape")
    elif closer == "finish":
        _action(mv_page, "finishMode").click()
    elif closer == "edit mode off":
        py(mv_page, "draw_edit_mode", False)
    else:
        py(mv_page, "draw", _env(2, BIG, SMALL, FAR))
    mv_page.wait_for_timeout(100)
    assert _confirm(mv_page).count() == 0
    assert mv_page.locator("#host .mv-select-box").count() == 0
    assert mv_page.locator("#host .mv-draw-doomed").count() == 0
    assert _draws(mv_page) == []
    assert len(_everywhere(mv_page)) == 3


def test_escape_while_armed_disarms(mv_page):
    _mount(mv_page)
    _trash(mv_page)
    _action(mv_page, "mvSelectArea").click()
    mv_page.keyboard.press("Escape")
    assert mv_page.locator(f"{_view('a')} .mv-draw-hint").inner_text() == "Right-click a vertex to delete it"
    x, y = _xy(mv_page, "a", -60.0, -1.0)
    mv_page.mouse.click(x, y)  # removal mode again: a click deletes BIG
    mv_page.wait_for_timeout(250)
    assert len(_draws(mv_page)) == 1
    assert len(_everywhere(mv_page)) == 2
