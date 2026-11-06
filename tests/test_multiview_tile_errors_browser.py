"""The tileErrors plugin through the widget (committed bundle, fake model): a
per-view badge while an imagery layer (``meta.reportErrors``) fails to load,
cleared when that layer is replaced or removed; the basemap and the layers
that do not report never raise it; one ``tileerror`` per failing layer."""

from tests._multiview_page import mount, mv_browser, mv_eval, mv_page, py  # noqa: F401

# A whole 1x1 transparent GIF. It must really load: the "ok" layers below
# report errors. The shorter "R0lGODlhAQABAAAAACw=" other tests use is not a
# valid image, so it fails to load.
TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"  # loads, no network
BAD = "data:image/png;base64,AAAA"  # not an image: every tile fails at once, no network


def _tile(id_, url, view="all", report=True, **kw):
    spec = {"id": id_, "view": view, "kind": "tile", "url": f"{url}#{id_}", "name": id_, "visible": True,
            "opacity": 1.0, "z": 1, "group": "alert", "meta": {"reportErrors": True} if report else {}}
    spec.update(kw)
    return spec


def _badge(page, view_id):
    return page.locator(f'#host .mv-view[data-view="{view_id}"] .mv-tile-error')


def _wait_badge(page, view_id):
    """Wait (5 s at most) until that view's badge shows."""
    page.wait_for_selector(f'#host .mv-view[data-view="{view_id}"] .mv-tile-error', state="visible", timeout=5000)


def _errors(page):
    return mv_eval(page, "mv => mv.plugin('tileErrors').errors()")


def test_a_failing_imagery_layer_shows_the_badge_on_its_view_only(mv_page):
    mount(mv_page, layers=[_tile("img", BAD, view="after"), _tile("ok", TILE)])
    _wait_badge(mv_page, "after")
    assert _badge(mv_page, "after").inner_text() == "Imagery failed to load"
    assert _badge(mv_page, "after").get_attribute("role") == "status"
    assert _badge(mv_page, "before").count() == 1, "every view has its (hidden) badge"
    assert not _badge(mv_page, "before").is_visible()
    assert _errors(mv_page) == {"before": [], "after": ["img"]}


def test_the_badge_clears_when_the_layer_is_replaced_or_removed(mv_page):
    mount(mv_page, layers=[_tile("img", BAD)])
    _wait_badge(mv_page, "before")
    _wait_badge(mv_page, "after")
    py(mv_page, "layers", [_tile("img", TILE)])  # the same id, a new spec: replaced
    mv_page.wait_for_timeout(300)
    assert not _badge(mv_page, "before").is_visible() and not _badge(mv_page, "after").is_visible()
    assert _errors(mv_page) == {"before": [], "after": []}
    py(mv_page, "layers", [_tile("img", BAD)])  # replaced again, by a failing layer
    _wait_badge(mv_page, "after")
    py(mv_page, "layers", [])  # removed
    mv_page.wait_for_timeout(300)
    assert not _badge(mv_page, "before").is_visible() and not _badge(mv_page, "after").is_visible()
    assert _errors(mv_page) == {"before": [], "after": []}


def test_layers_that_do_not_report_errors_and_the_basemap_show_no_badge(mv_page):
    mount(mv_page, basemap={"url": BAD},
          layers=[_tile("plain", BAD, report=False), _tile("img", BAD, view="after")])
    _wait_badge(mv_page, "after")  # the imagery failed: the basemap and "plain" have failed by now too
    mv_page.wait_for_timeout(500)
    assert not _badge(mv_page, "before").is_visible()
    assert _errors(mv_page) == {"before": [], "after": ["img"]}


def test_tileerror_is_emitted_once_per_layer(mv_page):
    mount(mv_page)
    mv_eval(mv_page, "mv => { window.__tileErrors = []; mv.on('tileerror', e => window.__tileErrors.push(e)); }")
    py(mv_page, "layers", [_tile("img", BAD)])
    _wait_badge(mv_page, "before")
    _wait_badge(mv_page, "after")
    mv_page.wait_for_timeout(500)
    assert mv_page.locator('#host .mv-view[data-view="after"] img.leaflet-tile').count() > 1, \
        "the view needs several failed tiles for this test to mean anything"
    events = mv_page.evaluate("() => window.__tileErrors")
    assert sorted((e["view"], e["id"]) for e in events) == [("after", "img"), ("before", "img")]
    py(mv_page, "layers", [_tile("img", BAD, opacity=0.5)])  # replaced: a new layer, reported again
    mv_page.wait_for_function("() => window.__tileErrors.length >= 4", timeout=5000)
    mv_page.wait_for_timeout(300)
    assert len(mv_page.evaluate("() => window.__tileErrors")) == 4


def test_the_badge_follows_the_labels_trait(mv_page):
    mount(mv_page, layers=[_tile("img", BAD, view="before")])
    _wait_badge(mv_page, "before")
    py(mv_page, "labels", {"tileErrors.badge": "No se pudieron cargar las imágenes"})
    assert _badge(mv_page, "before").inner_text() == "No se pudieron cargar las imágenes"
    assert _badge(mv_page, "after").text_content() == "No se pudieron cargar las imágenes"


def test_errors_lists_the_failed_ids_of_every_view(mv_page):
    mount(mv_page, layers=[_tile("a", BAD), _tile("b", BAD, view="after"), _tile("ok", TILE)])
    _wait_badge(mv_page, "before")
    mv_page.wait_for_function(
        "() => document.getElementById('host')._multiview.plugin('tileErrors').errors().after.length === 2",
        timeout=5000)
    got = _errors(mv_page)
    assert {k: sorted(v) for k, v in got.items()} == {"before": ["a"], "after": ["a", "b"]}
    assert mv_page.locator("#host .mv-tile-error").count() == 2, "one badge per view, not one per layer"
