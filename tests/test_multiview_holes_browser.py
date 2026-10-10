"""The holes plugin through the widget (committed bundle, fake model): the
controls following edit mode, Fill holes on every polygon or on the selected
one, the Remove hole click mode with its hint and Esc, a click off every
polygon clearing the selection, the labels, the API and the install order."""

from tests._multiview_page import mount, mv_browser, mv_eval, mv_page, py, writes  # noqa: F401

# Rings are [lng, lat]. At zoom 13 a pixel is 1.7e-4 degrees: SHELL is about
# 233 px wide, each hole about 58 px, so everything fits a 416 px view.
SHELL = [[-60.02, -1.02], [-59.98, -1.02], [-59.98, -0.98], [-60.02, -0.98], [-60.02, -1.02]]
HOLE_A = [[-60.015, -1.015], [-60.005, -1.015], [-60.005, -1.005], [-60.015, -1.005], [-60.015, -1.015]]
HOLE_B = [[-59.995, -0.995], [-59.985, -0.995], [-59.985, -0.985], [-59.995, -0.985], [-59.995, -0.995]]
IN_A = (-60.01, -1.01)  # the middle of HOLE_A
IN_B = (-59.99, -0.99)  # the middle of HOLE_B

# Two polygons side by side, each with one hole, around (-60.0, -0.99).
LEFT = [[-60.02, -1.0], [-60.005, -1.0], [-60.005, -0.985], [-60.02, -0.985], [-60.02, -1.0]]
LEFT_HOLE = [[-60.015, -0.995], [-60.01, -0.995], [-60.01, -0.99], [-60.015, -0.99], [-60.015, -0.995]]
RIGHT = [[-59.995, -1.0], [-59.98, -1.0], [-59.98, -0.985], [-59.995, -0.985], [-59.995, -1.0]]
RIGHT_HOLE = [[-59.99, -0.995], [-59.985, -0.995], [-59.985, -0.99], [-59.99, -0.99], [-59.99, -0.995]]
ON_RIGHT = (-59.982, -0.987)  # on the right polygon, off its hole
BETWEEN = (-60.0, -0.99)  # the gap between the two polygons


def _poly(*rings):
    return {"type": "Feature", "properties": {"style": {"color": "blue"}},
            "geometry": {"type": "Polygon", "coordinates": [list(r) for r in rings]}}


def _mount(page, *features, edit=True, center=(-1.0, -60.0), **state_):
    """Two views (before, after), zoom 13 on ``center`` ([lat, lng]), the
    drawing ``features`` as a host write at gen 1."""
    mount(page, host_style="width:840px;height:420px",
          view={"center": list(center), "zoom": 13, "n": 1, "origin": "host"},
          draw={"fc": {"type": "FeatureCollection", "features": list(features)}, "gen": 1, "seq": 0,
                "origin": "host"},
          draw_edit_mode=edit, **state_)
    page.wait_for_timeout(250)  # Geoman enables layers added in edit mode through a 100 ms throttle (R11)


def _draws(page):
    return writes(page, "draw")


def _holes(page):
    """Interior rings per feature, in the last drawing the frontend wrote."""
    return [len(f["geometry"]["coordinates"]) - 1 for f in _draws(page)[-1]["fc"]["features"]]


def _shown_holes(page, view_id):
    """Interior rings per drawing layer on that view's map."""
    return mv_eval(page, """(mv, id) => { const out = []; const view = mv.views.find(v => v.id === id);
        view.map.eachLayer(l => { if (l._mvDrawId !== undefined) out.push(l.toGeoJSON(false).geometry.coordinates.length - 1); });
        return out; }""", view_id)


def _click(page, view_id, lng, lat, button="left"):
    """A click at the page position of [lng, lat] in one view, then 250 ms."""
    x, y = mv_eval(page, """(mv, [id, lng, lat]) => { const view = mv.views.find(v => v.id === id);
        const p = view.map.latLngToContainerPoint([lat, lng]); const r = view.map.getContainer().getBoundingClientRect();
        return [r.left + p.x, r.top + p.y]; }""", [view_id, lng, lat])
    page.mouse.click(x, y, button=button)
    page.wait_for_timeout(250)


def _press(page, view_id, cls):
    """Click the hole bar's ``a.<cls>`` on one view (fails at once when it is not there)."""
    link = page.locator(f'.mv-view[data-view="{view_id}"] a.{cls}')
    assert link.count() == 1, f"no a.{cls} on the {view_id} view"
    link.click()
    page.wait_for_timeout(250)


def _selection(page):
    return mv_eval(page, "mv => mv.plugin('draw').selection()")


def test_the_hole_controls_follow_edit_mode(mv_page):
    _mount(mv_page, _poly(SHELL, HOLE_A), edit=False)
    assert mv_page.locator("#host .mv-hole-bar").count() == 0
    py(mv_page, "draw_edit_mode", True)
    assert mv_page.locator("#host .mv-hole-bar").count() == 2, "one bar per view"
    fill = mv_page.locator('.mv-view[data-view="before"] a.mv-fill-holes')
    remove = mv_page.locator('.mv-view[data-view="before"] a.mv-remove-hole')
    assert (fill.get_attribute("title"), fill.get_attribute("aria-label")) == ("Fill holes", "Fill holes")
    assert (remove.get_attribute("title"), remove.get_attribute("role")) == ("Remove hole", "button")
    py(mv_page, "draw_edit_mode", False)
    assert mv_page.locator("#host .mv-hole-bar").count() == 0


def test_the_hole_controls_show_when_edit_mode_is_on_before_holes_installs(mv_page):
    # The draw binding turns edit mode on as soon as draw installs, before holes.
    _mount(mv_page, _poly(SHELL, HOLE_A), edit=True)
    assert mv_page.locator("#host .mv-hole-bar").count() == 2


def test_fill_holes_with_nothing_selected_fills_every_polygon(mv_page):
    _mount(mv_page, _poly(SHELL, HOLE_A, HOLE_B), _poly(SHELL, HOLE_A))
    _press(mv_page, "before", "mv-fill-holes")
    assert len(_draws(mv_page)) == 1, "one commit, one write"
    assert _holes(mv_page) == [0, 0]
    assert _draws(mv_page)[0]["fc"]["features"][0]["geometry"]["coordinates"] == [SHELL]
    assert _shown_holes(mv_page, "before") == [0, 0] and _shown_holes(mv_page, "after") == [0, 0]
    _press(mv_page, "after", "mv-fill-holes")
    assert len(_draws(mv_page)) == 1, "nothing left to fill: no second write"


def test_fill_holes_on_a_selected_polygon_leaves_the_others(mv_page):
    _mount(mv_page, _poly(LEFT, LEFT_HOLE), _poly(RIGHT, RIGHT_HOLE), center=(-0.99, -60.0))
    _click(mv_page, "before", *ON_RIGHT)
    assert _selection(mv_page) is not None, "the click did not select the right polygon"
    _press(mv_page, "before", "mv-fill-holes")
    assert _holes(mv_page) == [1, 0]
    assert _shown_holes(mv_page, "after") == [1, 0]


def test_remove_hole_mode_removes_the_clicked_hole_only_and_esc_leaves(mv_page):
    _mount(mv_page, _poly(SHELL, HOLE_A, HOLE_B))
    _press(mv_page, "after", "mv-remove-hole")
    assert mv_page.locator("#host a.mv-remove-hole.mv-active").count() == 2, "both views show the mode"
    assert mv_page.locator("#host .leaflet-container.mv-removing").count() == 2
    assert mv_page.locator("#host .mv-holes-hint").count() == 2
    assert "Esc" in mv_page.locator('.mv-view[data-view="after"] .mv-holes-hint').inner_text()
    assert mv_page.locator("#host .mv-draw-hint:visible").count() == 0, "the draw hint gives way"
    _click(mv_page, "after", *IN_B)
    assert len(_draws(mv_page)) == 1
    assert _holes(mv_page) == [1]
    assert _draws(mv_page)[0]["fc"]["features"][0]["geometry"]["coordinates"] == [SHELL, HOLE_A]
    assert _shown_holes(mv_page, "before") == [1]
    mv_page.keyboard.press("Escape")
    mv_page.wait_for_timeout(100)
    assert mv_page.locator("#host a.mv-remove-hole.mv-active").count() == 0
    assert mv_page.locator("#host .mv-holes-hint").count() == 0
    assert mv_page.locator("#host .mv-removing").count() == 0
    assert mv_page.locator("#host .mv-draw-hint:visible").count() == 2
    _click(mv_page, "after", *IN_A)  # the mode is off: nothing happens
    assert len(_draws(mv_page)) == 1


def test_the_remove_hole_button_toggles_the_mode_and_leaving_edit_mode_ends_it(mv_page):
    _mount(mv_page, _poly(SHELL, HOLE_A))
    _press(mv_page, "before", "mv-remove-hole")
    assert mv_eval(mv_page, "mv => mv.plugin('holes').removeMode()") is True
    _press(mv_page, "before", "mv-remove-hole")
    assert mv_eval(mv_page, "mv => mv.plugin('holes').removeMode()") is False
    _press(mv_page, "before", "mv-remove-hole")
    py(mv_page, "draw_edit_mode", False)
    assert mv_eval(mv_page, "mv => mv.plugin('holes').removeMode()") is False
    assert mv_page.locator("#host .mv-removing").count() == 0
    assert mv_page.locator("#host .mv-holes-hint").count() == 0


def test_a_click_off_every_polygon_clears_the_selection(mv_page):
    _mount(mv_page, _poly(LEFT, LEFT_HOLE), _poly(RIGHT, RIGHT_HOLE), center=(-0.99, -60.0))
    _click(mv_page, "after", *ON_RIGHT)
    assert _selection(mv_page) is not None
    _click(mv_page, "after", *BETWEEN)
    assert _selection(mv_page) is None
    assert mv_page.locator("#host path.mv-draw-selected").count() == 0
    assert _draws(mv_page) == [], "selecting and deselecting are not edits"


def test_the_hole_labels_follow_the_labels_trait(mv_page):
    _mount(mv_page, _poly(SHELL, HOLE_A))
    py(mv_page, "labels", {"holes.fill": "Rellenar huecos", "holes.remove": "Quitar hueco",
                           "holes.hint": "Haga clic dentro de un hueco para quitarlo · Esc para salir"})
    assert mv_page.locator('.mv-view[data-view="after"] a.mv-fill-holes').get_attribute("title") == "Rellenar huecos"
    assert mv_page.locator('.mv-view[data-view="after"] a.mv-remove-hole').get_attribute("aria-label") == "Quitar hueco"
    _press(mv_page, "after", "mv-remove-hole")
    assert mv_page.locator('.mv-view[data-view="before"] .mv-holes-hint').inner_text() == \
        "Haga clic dentro de un hueco para quitarlo · Esc para salir"


def test_the_api_fills_and_ignores_the_remove_mode_outside_edit_mode(mv_page):
    _mount(mv_page, _poly(SHELL, HOLE_A, HOLE_B), edit=False)
    mv_eval(mv_page, "mv => mv.plugin('holes').setRemoveMode(true)")
    assert mv_eval(mv_page, "mv => mv.plugin('holes').removeMode()") is False
    mv_eval(mv_page, "mv => mv.plugin('holes').fillHoles()")
    mv_page.wait_for_timeout(250)
    assert _holes(mv_page) == [0]


def test_holes_needs_draw_installed_first(mv_page):
    mount(mv_page, plugins=["layerMenu", "holes", "draw"])
    strip = mv_page.locator("#host .mv-error")
    assert strip.count() == 1
    assert "holes: holes requires draw, which is not installed yet" in strip.inner_text()
    assert mv_eval(mv_page, "mv => mv.plugin('draw') !== undefined"), "draw still installs after the failure"
