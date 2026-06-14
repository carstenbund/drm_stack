"""Integration: HTML <button> -> interactive layer, and page navigation.

Covers the full declarative chain — drm_composer compiles a <button> to an
interactive drm_screen layer — and the page-navigation app's history/back logic.
Headless; no display or input hardware.
"""

from drm_composer import parse_scene, paint_scene
from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import CreateLayer
from drm_touch import TouchEvent

import page_demo


def test_button_compiles_to_interactive_layer():
    html = ('<screen width="800" height="480"><layer id="p" z="0">'
            '<button id="go" x="10" y="20" w="120" h="50" color="#2a7">Go</button>'
            '</layer></screen>')
    batch = paint_scene(parse_scene(html))
    buttons = [c for c in batch if isinstance(c, CreateLayer) and c.interactive]
    assert len(buttons) == 1
    b = buttons[0]
    assert b.hit_id == "go"
    assert (b.x, b.y, b.width, b.height) == (10, 20, 120, 50)
    # the page layer itself stays non-interactive
    page = next(c for c in batch if isinstance(c, CreateLayer) and c.name == "p")
    assert not page.interactive


def test_anchor_compiles_to_link_with_href_hit_id():
    html = ('<screen width="800" height="480"><layer id="p" z="0">'
            '<a href="next.html" x="10" y="20" w="120" h="50">Next</a>'
            '</layer></screen>')
    batch = paint_scene(parse_scene(html))
    links = [c for c in batch if isinstance(c, CreateLayer) and c.interactive]
    assert len(links) == 1
    # a link reuses the button machinery; its hit_id encodes the href
    assert links[0].hit_id == "href:next.html"


def _app():
    service = ScreenService(DrmDisplayBackend(device="dummy", width=1024, height=600))
    app = page_demo.PageApp(service)
    app.goto("home")
    service.render_once()
    return app


def _tap(app, hit_id):
    c = app.button_center(hit_id)
    assert c is not None, f"{hit_id} not on page {app.current}"
    app.on_event(TouchEvent("down", *c))
    app.service.render_once()


def test_navigation_history_and_back_restore():
    app = _app()
    _tap(app, "href:menu.html");     assert app.current == "menu"
    _tap(app, "href:settings.html"); assert app.current == "settings"
    _tap(app, "back");               assert app.current == "menu"   # restored
    _tap(app, "back");               assert app.current == "home"   # restored
    app.back()  # history empty (home has no Back) -> no-op
    assert app.current == "home"


def test_slideshow_chain_and_quit():
    app = _app()
    _tap(app, "href:slide1.html"); assert app.current == "slide1"
    _tap(app, "href:slide2.html"); assert app.current == "slide2"
    _tap(app, "back");             assert app.current == "slide1"
    assert app.button_center("quit") is None    # slides have no Quit
    assert app.running

    home = _app()
    assert home.button_center("quit") is not None
    home.on_event(TouchEvent("down", *home.button_center("quit")))
    assert home.running is False
