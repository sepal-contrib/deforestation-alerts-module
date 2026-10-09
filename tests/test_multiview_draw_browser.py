"""The draw plugin through the widget (committed bundle, fake model), in three
views: one write per finished gesture, follower rebuilds, self-intersection
resets, host writes mid-gesture, the envelope's gen/seq, overlays that stay
non-editable, hideWhileEditing, the selection, the toolbar and the hint."""

import pytest

from tests._multiview_page import (  # noqa: F401
    mount,
    mv_browser,
    mv_eval,
    mv_page,
    py,
    writes,
)

TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
VIEWS = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
D = 0.004  # half the square's side in degrees: about 47 px at zoom 14
SQUARE = [[-60.0 - D, -1.0 - D], [-60.0 + D, -1.0 - D], [-60.0 + D, -1.0 + D], [-60.0 - D, -1.0 + D], [-60.0 - D, -1.0 - D]]
SW, SE, NE, NW = SQUARE[:4]


def _feature(ring, **style):
    return {"type": "Feature", "properties": {"style": style} if style else {},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _env(gen, *rings):
    return {"fc": {"type": "FeatureCollection", "features": [_feature(r) for r in rings]},
            "gen": gen, "seq": 0, "origin": "host"}


def _mount(page, rings=(SQUARE,), edit=True, **state_):
    mount(page, host_style="width:840px;height:420px", views=VIEWS, draw=_env(1, *rings), draw_edit_mode=edit,
          **state_)
    page.wait_for_timeout(250)  # Geoman enables layers added in edit mode through a 100 ms throttle


def _drawn(page, view_id):
    """The drawing layers on that view's map, as GeoJSON geometries."""
    return mv_eval(page, """(mv, id) => { const out = []; const view = mv.views.find(v => v.id === id);
        view.map.eachLayer(l => { if (l._mvDrawId !== undefined) out.push(l.toGeoJSON(false).geometry); });
        return out; }""", view_id)


def _rings(page, view_id):
    return [g["coordinates"][0] for g in _drawn(page, view_id)]


def _same_everywhere(page):
    a = _drawn(page, "a")
    assert _drawn(page, "b") == a and _drawn(page, "c") == a
    return a


def _xy(page, view_id, lng, lat):
    """The page position of a [lng, lat] point in one view."""
    return mv_eval(page, """(mv, [id, lng, lat]) => { const view = mv.views.find(v => v.id === id);
        const p = view.map.latLngToContainerPoint([lat, lng]); const r = view.map.getContainer().getBoundingClientRect();
        return [r.left + p.x, r.top + p.y]; }""", [view_id, lng, lat])


def _mid(p, q):
    return [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2]


def _press(page, view_id, lng, lat):
    x, y = _xy(page, view_id, lng, lat)
    page.mouse.move(x, y)
    page.mouse.down()
    return x, y


def _drag(page, view_id, frm, to):
    """Press at ``frm``, move to ``to`` in 12 steps, release, let Geoman settle."""
    _press(page, view_id, *frm)
    x, y = _xy(page, view_id, *to)
    page.mouse.move(x, y, steps=12)
    page.mouse.up()
    page.wait_for_timeout(250)


def _layer_ids(page):
    """The Leaflet ids of the drawing layers, per view."""
    return mv_eval(page, """mv => mv.views.map(v => { const out = [];
        v.map.eachLayer(l => { if (l._mvDrawId !== undefined) out.push(l._leaflet_id); }); return out; })""")


def _draws(page):
    return writes(page, "draw")


def _vertices(page, view_id):
    return page.locator(f'.mv-view[data-view="{view_id}"] .marker-icon:not(.marker-icon-middle)').count()


# --- one write per finished gesture --------------------------------------------------


def test_a_vertex_drag_writes_once_and_rebuilds_the_other_views(mv_page):
    _mount(mv_page)
    ids = _layer_ids(mv_page)
    to = [NE[0] + D, NE[1] + D]
    _drag(mv_page, "b", NE, to)
    (write,) = _draws(mv_page)
    _same_everywhere(mv_page)
    (ring,) = _rings(mv_page, "a")
    assert ring[2] == pytest.approx(to, abs=1e-4)  # a pixel at zoom 14 is 8.6e-5 deg
    assert write["fc"]["features"][0]["geometry"]["coordinates"][0] == ring, "the write is what every view shows"
    assert (write["gen"], write["seq"], write["origin"]) == (1, 1, "user")
    after = _layer_ids(mv_page)
    assert after[1] == ids[1], "the source view keeps its Geoman layer (and its edit state)"
    assert after[0] != ids[0] and after[2] != ids[2], "the followers are rebuilt"


def test_a_midpoint_drag_writes_nothing_until_it_is_released(mv_page):
    _mount(mv_page)
    south = _mid(SW, SE)
    _press(mv_page, "a", *south)
    x, y = _xy(mv_page, "a", south[0], south[1] - D)
    mv_page.mouse.move(x, y, steps=12)
    mv_page.wait_for_timeout(300)
    assert _draws(mv_page) == [], "the midpoint's pm:edit/pm:vertexadded before release are not a commit"
    assert len(_rings(mv_page, "b")[0]) == 5, "followers are not touched mid-gesture"
    mv_page.mouse.up()
    mv_page.wait_for_timeout(250)
    assert len(_draws(mv_page)) == 1
    ring = _rings(mv_page, "a")[0]
    assert len(ring) == 6 and ring[1] == pytest.approx([south[0], south[1] - D], abs=1e-4)
    _same_everywhere(mv_page)


def test_a_midpoint_click_adds_a_vertex_with_one_write(mv_page):
    _mount(mv_page)
    x, y = _xy(mv_page, "c", *_mid(SE, NE))
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(250)
    assert len(_draws(mv_page)) == 1
    assert len(_rings(mv_page, "c")[0]) == 6
    _same_everywhere(mv_page)


def test_a_right_click_on_a_vertex_removes_it_with_one_write(mv_page):
    _mount(mv_page)
    x, y = _xy(mv_page, "a", *NW)
    mv_page.mouse.click(x, y, button="right")
    mv_page.wait_for_timeout(250)
    assert len(_draws(mv_page)) == 1
    assert _rings(mv_page, "b")[0] == [SW, SE, NE, SW]
    _same_everywhere(mv_page)


def test_a_self_intersecting_corner_drag_is_reset_without_a_write(mv_page):
    _mount(mv_page)
    _drag(mv_page, "a", SW, [NE[0] + D, -1.0])  # SW past the east edge: a bow tie
    assert _draws(mv_page) == []
    assert _rings(mv_page, "a") == [SQUARE] and _rings(mv_page, "b") == [SQUARE] and _rings(mv_page, "c") == [SQUARE]


def test_a_self_intersecting_midpoint_drag_puts_the_source_view_back(mv_page):
    # Geoman's reset keeps the vertex it inserted for the midpoint (it copied the
    # ring after the insertion): the plugin rebuilds the source from the collection.
    _mount(mv_page)
    _drag(mv_page, "b", _mid(SW, SE), [-60.0, -1.0 + 2 * D])  # the south midpoint past the north edge
    assert _draws(mv_page) == []
    for view in ("a", "b", "c"):
        assert _rings(mv_page, view) == [SQUARE], view
    assert _vertices(mv_page, "b") == 4, "the rebuilt source view is editable again"


def test_removal_mode_deletes_a_polygon_with_one_write_and_editing_resumes(mv_page):
    _mount(mv_page, rings=(SQUARE, [[p[0] + 3 * D, p[1]] for p in SQUARE]))
    mv_page.locator('.mv-view[data-view="a"] .leaflet-pm-icon-delete').click()
    x, y = _xy(mv_page, "a", -60.0, -1.0)
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(250)
    assert len(_draws(mv_page)) == 1
    assert len(_draws(mv_page)[0]["fc"]["features"]) == 1
    assert len(_same_everywhere(mv_page)) == 1
    mv_page.locator('.mv-view[data-view="a"] .leaflet-pm-icon-delete').click()  # removal mode off
    mv_page.wait_for_timeout(250)
    assert _vertices(mv_page, "a") == 4, "removal mode turned editing off; leaving it turns it back on"


def test_a_pm_edit_outside_a_gesture_never_commits(mv_page):
    _mount(mv_page)
    mv_eval(mv_page, """mv => { mv.views[0].map.eachLayer(l => {
        if (l._mvDrawId === undefined) return;
        l.setLatLngs([[-1.0, -60.0], [-1.0, -59.99], [-0.99, -59.99]]);
        l.fire('pm:edit', {layer: l, shape: 'Polygon'}); }); }""")
    mv_page.wait_for_timeout(250)
    assert _draws(mv_page) == []
    assert _rings(mv_page, "b") == [SQUARE], "nothing reached the followers"


def test_drawing_a_polygon_adds_it_to_every_view(mv_page):
    _mount(mv_page, rings=())
    mv_page.locator('.mv-view[data-view="a"] .leaflet-pm-icon-polygon').click()
    for lng, lat in ([-60.004, -1.004], [-59.996, -1.004], [-60.0, -0.996], [-60.004, -1.004]):
        x, y = _xy(mv_page, "a", lng, lat)
        mv_page.mouse.click(x, y)
        mv_page.wait_for_timeout(80)
    mv_page.wait_for_timeout(250)
    assert len(_draws(mv_page)) == 1
    (feature,) = _draws(mv_page)[0]["fc"]["features"]
    assert feature["properties"]["style"]["color"] == "#2196F3"
    assert len(feature["geometry"]["coordinates"][0]) == 4
    assert len(_same_everywhere(mv_page)) == 1
    color = mv_eval(mv_page, """mv => { let c = null; mv.views[2].map.eachLayer(l => { if (l._mvDrawId) c = l.options.color; }); return c; }""")
    assert color == "#2196F3"


def test_the_draw_button_leaves_edit_mode_on(mv_page):
    # Geoman's toolbar clicks every toggled button before its own, and the
    # (hidden) edit button is toggled by enableGlobalEditMode: the plugin
    # untoggles it, or "draw polygon" would end editing.
    _mount(mv_page)
    mv_page.locator('.mv-view[data-view="a"] .leaflet-pm-icon-polygon').click()
    mv_page.wait_for_timeout(100)
    assert mv_eval(mv_page, "mv => mv.views[0].map.pm.globalEditModeEnabled()")
    mv_page.locator('.mv-view[data-view="a"] .leaflet-pm-icon-polygon').click()  # draw mode off again
    mv_page.wait_for_timeout(250)
    assert mv_eval(mv_page, "mv => mv.views[0].map.pm.globalEditModeEnabled()")
    assert _vertices(mv_page, "a") == 4
    assert _draws(mv_page) == []


# --- Python writes ---------------------------------------------------------------------


def test_a_python_draw_write_mid_gesture_rebuilds_all_views_without_echo(mv_page):
    _mount(mv_page)
    _press(mv_page, "a", *NE)
    x, y = _xy(mv_page, "a", NE[0] + D, NE[1] + D)
    mv_page.mouse.move(x, y, steps=8)  # a vertex drag in progress in view a
    other = [[p[0] + 2 * D, p[1] - 2 * D] for p in SQUARE]
    py(mv_page, "draw", _env(2, other))
    mv_page.mouse.move(x + 20, y + 20, steps=4)
    mv_page.mouse.up()
    mv_page.wait_for_timeout(400)
    assert _draws(mv_page) == [], "Python's write is not an edit, and the interrupted drag commits nothing"
    for view in ("a", "b", "c"):
        assert _rings(mv_page, view) == [other], view
    # The views are still editable, with the adopted gen.
    x, y = _xy(mv_page, "b", *_mid(other[0], other[1]))
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(250)
    (write,) = _draws(mv_page)
    assert (write["gen"], write["seq"], write["origin"]) == (2, 1, "user")


def test_a_host_write_with_an_old_gen_is_ignored(mv_page):
    _mount(mv_page)
    other = [[p[0] + 2 * D, p[1]] for p in SQUARE]
    py(mv_page, "draw", _env(3, other))
    py(mv_page, "draw", _env(2, SQUARE))
    py(mv_page, "draw", {**_env(5, SQUARE), "origin": "user"})  # an echo of a user write never replaces
    mv_page.wait_for_timeout(250)
    assert _rings(mv_page, "a") == [other]


def test_user_writes_carry_the_adopted_gen_and_a_growing_seq(mv_page):
    _mount(mv_page)
    py(mv_page, "draw", _env(4, SQUARE))
    mv_page.wait_for_timeout(250)
    for a, b in ((SW, SE), (SE, NE)):
        x, y = _xy(mv_page, "a", *_mid(a, b))
        mv_page.mouse.click(x, y)
        mv_page.wait_for_timeout(250)
    assert [(w["gen"], w["seq"], w["origin"]) for w in _draws(mv_page)] == [(4, 1, "user"), (4, 2, "user")]
    assert len(_draws(mv_page)[-1]["fc"]["features"][0]["geometry"]["coordinates"][0]) == 7
    assert "id" not in _draws(mv_page)[-1]["fc"]["features"][0], "internal ids never leave JS"


def test_feature_styles_are_applied_and_kept(mv_page):
    mount(mv_page, host_style="width:840px;height:420px", views=VIEWS, draw_edit_mode=True,
          draw={"fc": {"type": "FeatureCollection", "features": [_feature(SQUARE, color="#ff0000", pane="overlayPane")]},
                "gen": 1, "seq": 0, "origin": "host"})
    mv_page.wait_for_timeout(250)
    layer = mv_eval(mv_page, """mv => { let o = null; mv.views[1].map.eachLayer(l => { if (l._mvDrawId) o = [l.options.color, l.options.pane]; }); return o; }""")
    assert layer == ["#ff0000", "mv-drawing"], "the style applies; the drawing pane wins over a style's pane"
    x, y = _xy(mv_page, "a", *_mid(SW, SE))
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(250)
    assert _draws(mv_page)[0]["fc"]["features"][0]["properties"]["style"] == {"color": "#ff0000", "pane": "overlayPane"}


def test_a_feature_without_geometry_is_kept_but_not_drawn(mv_page):
    mount(mv_page, host_style="width:840px;height:420px", views=VIEWS, draw_edit_mode=True,
          draw={"fc": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": None},
                                                                 _feature(SQUARE)]},
                "gen": 1, "seq": 0, "origin": "host"})
    mv_page.wait_for_timeout(250)
    assert len(_drawn(mv_page, "a")) == 1
    x, y = _xy(mv_page, "a", *_mid(SW, SE))
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(250)
    features = _draws(mv_page)[0]["fc"]["features"]
    assert features[0]["geometry"] is None and len(features[1]["geometry"]["coordinates"][0]) == 6


# --- edit mode: overlays, hideWhileEditing, selection, toolbar, hint ---------------------


def test_overlays_never_get_vertex_markers(mv_page):
    overlay = {"id": "box", "view": "all", "kind": "geojson", "name": "box", "visible": True, "opacity": 1.0, "z": 1,
               "data": {"type": "FeatureCollection", "features": [_feature([[p[0] + 3 * D, p[1]] for p in SQUARE])]},
               "style": {"color": "#000"}, "meta": {}}
    _mount(mv_page, layers=[overlay])
    for view in ("a", "b", "c"):
        assert _vertices(mv_page, view) == 4, "the drawing's four vertices only"


def test_hide_while_editing_layers_are_hidden_only_in_edit_mode(mv_page):
    tile = {"id": "bb", "view": "all", "kind": "tile", "url": f"{TILE}#bb", "name": "bb", "visible": True,
            "opacity": 1.0, "z": 1, "meta": {"hideWhileEditing": True}}
    _mount(mv_page, edit=False, layers=[tile])
    shown = "mv => mv.views.map(v => { let n = 0; v.map.eachLayer(l => { if (l._url && l._url.endsWith('#bb')) n++; }); return n; })"
    assert mv_eval(mv_page, shown) == [1, 1, 1]
    py(mv_page, "draw_edit_mode", True)
    assert mv_eval(mv_page, shown) == [0, 0, 0]
    py(mv_page, "draw_edit_mode", False)
    assert mv_eval(mv_page, shown) == [1, 1, 1]


def test_a_click_selects_a_polygon_in_every_view_until_a_host_write(mv_page):
    _mount(mv_page)
    x, y = _xy(mv_page, "b", -60.0 + D / 2, -1.0 + D / 2)
    mv_page.mouse.click(x, y)
    mv_page.wait_for_timeout(100)
    assert mv_page.locator("#host path.mv-draw-selected").count() == 3
    assert mv_eval(mv_page, "mv => mv.plugin('draw').selection()") is not None
    py(mv_page, "draw", _env(2, SQUARE))
    assert mv_page.locator("#host path.mv-draw-selected").count() == 0
    assert mv_eval(mv_page, "mv => mv.plugin('draw').selection()") is None
    assert _draws(mv_page) == [], "selecting is not an edit"


def test_leaving_edit_mode_removes_the_toolbar_and_hint_and_keeps_the_drawing(mv_page):
    _mount(mv_page)
    assert mv_page.locator("#host .leaflet-pm-toolbar").count() >= 3
    assert mv_page.locator("#host .mv-draw-hint").count() == 3
    assert mv_page.locator("#host .mv-draw-hint").first.inner_text() == "Right-click a vertex to delete it"
    py(mv_page, "labels", {"draw.hint": "Clic derecho en un vértice para borrarlo"})
    assert mv_page.locator("#host .mv-draw-hint").first.inner_text() == "Clic derecho en un vértice para borrarlo"
    py(mv_page, "draw_edit_mode", False)
    assert mv_page.locator("#host .leaflet-pm-toolbar").count() == 0
    assert mv_page.locator("#host .mv-draw-hint").count() == 0
    assert mv_page.locator("#host .marker-icon").count() == 0
    assert _rings(mv_page, "c") == [SQUARE]
    py(mv_page, "draw_edit_mode", True)
    mv_page.wait_for_timeout(250)
    assert _vertices(mv_page, "c") == 4
