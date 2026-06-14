"""Integration: drm_touch -> drm_screen hit-testing + pointer overlay.

Headless, using DummyTouch (scripted events) — no hardware. Exercises the
app-in-control input path: events -> app queue -> hit_test -> (the app would
submit feedback); and the autonomous pointer cursor fed via fan_out().
"""

import queue

from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import CreateLayer, _cursor_hotspot, _POINTER_NAME
from drm_touch import DummyTouch, TouchReader, TouchEvent, fan_out

from conftest import W, H


def _service():
    return ScreenService(DrmDisplayBackend(device="dummy", width=W, height=H))


def _button(service, hit_id="ok"):
    # 80x40 button at (40, 30); interactive
    service.submit([CreateLayer("btn", 80, 40, x=40, y=30, z=10,
                                interactive=True, hit_id=hit_id)])
    service.render_once()


# ── hit_test query ────────────────────────────────────────────────────────────

def test_hit_test_bounds():
    s = _service()
    _button(s)
    assert s.hit_test(60, 50) == "ok"       # inside
    assert s.hit_test(40, 30) == "ok"        # top-left corner (inclusive)
    assert s.hit_test(120, 70) is None       # bottom-right (exclusive)
    assert s.hit_test(5, 5) is None          # well outside


def test_hit_test_topmost_wins():
    s = _service()
    _button(s, hit_id="under")
    # a second interactive layer on top, overlapping, higher z
    s.submit([CreateLayer("over", 80, 40, x=40, y=30, z=20,
                          interactive=True, hit_id="over")])
    s.render_once()
    assert s.hit_test(60, 50) == "over"
    s.submit([CreateLayer("non", W, H, z=30)])   # non-interactive on top
    s.render_once()
    assert s.hit_test(60, 50) == "over"          # ignored: not interactive


# ── autonomous pointer overlay via fan_out ────────────────────────────────────

def test_pointer_overlay_tracks():
    s = _service()
    app_q = queue.Queue()
    sink = fan_out(s.submit, app_q)

    sink(TouchEvent("hover", 100, 60))
    s.render_once()

    p = s.composer.layers[_POINTER_NAME]
    hx, hy = _cursor_hotspot()
    assert p.visible and (p.x, p.y) == (100 - hx, 60 - hy)   # hotspot-centred
    assert not p.interactive                                  # never hit-tested
    assert s.hit_test(100, 60) is None                        # cursor isn't a target

    ev = app_q.get_nowait()
    assert (ev.phase, ev.x, ev.y) == ("hover", 100, 60)       # app still sees it


def test_pointer_hidden_on_release():
    s = _service()
    app_q = queue.Queue()
    sink = fan_out(s.submit, app_q)
    sink(TouchEvent("down", 50, 50)); s.render_once()
    assert s.composer.layers[_POINTER_NAME].visible
    sink(TouchEvent("up", 50, 50)); s.render_once()
    assert not s.composer.layers[_POINTER_NAME].visible       # visible=False on up


# ── full path: a scripted reader drives taps the app hit-tests ────────────────

def test_dummy_reader_drives_taps():
    s = _service()
    _button(s)
    app_q = queue.Queue()
    sink = fan_out(s.submit, app_q)

    script = [TouchEvent("down", 60, 50),   # inside the button
              TouchEvent("up", 60, 50),
              TouchEvent("down", 5, 5)]      # outside
    TouchReader(DummyTouch(script), sink).run_blocking()   # synchronous
    s.render_once()

    # the app drains its queue and hit-tests each press (as the app loop would)
    hits = []
    while not app_q.empty():
        ev = app_q.get_nowait()
        if ev.phase == "down":
            hits.append(s.hit_test(ev.x, ev.y))
    assert hits == ["ok", None]
