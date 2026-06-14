"""End-to-end integration tests across the whole drm_stack.

Covers the package boundaries that unit tests in each repo can't:
  - drm_composer HTML  -> command batch  (the contract)
  - command batch      -> drm_screen layers/composite
  - drm_screen frame   -> drm_display RGBA->BGRA boundary
"""

import time

import numpy as np
import pytest

from drm_composer import parse_scene, paint_scene, Compositor
from drm_screen import ScreenService, InProcessTarget
from drm_screen.commands import (
    CreateLayer, PlaceRawBuffer, HideLayer, ShowLayer, to_wire, from_wire,
)

from conftest import W, H, render, raw_layer, solid


SCENE = f"""
<screen width="{W}" height="{H}">
  <layer id="background" z="0">
    <box x="0" y="0" w="{W}" h="{H}" color="#141e3c" />
  </layer>
  <layer id="status" z="10">
    <box x="10" y="10" w="120" h="40" color="#000000aa" />
    <text x="16" y="20" size="14" color="#ffffff">ready</text>
  </layer>
</screen>
"""


# ── drm_composer: parse + compile ─────────────────────────────────────────────

def test_parser_builds_scene_tree():
    scene = parse_scene(SCENE)
    assert (scene.width, scene.height) == (W, H)
    assert [l.id for l in scene.layers] == ["background", "status"]
    assert scene.layers[1].z == 10
    # status layer has a box + text child
    kinds = [type(c).__name__ for c in scene.layers[1].children]
    assert kinds == ["BoxNode", "TextNode"]


def test_painter_emits_command_contract():
    batch = paint_scene(parse_scene(SCENE))
    # one CreateLayer + one PlaceRawBuffer per layer, in order
    assert [type(c).__name__ for c in batch] == [
        "CreateLayer", "PlaceRawBuffer", "CreateLayer", "PlaceRawBuffer",
    ]
    # bitmaps are full-screen RGBA byte blobs
    raw = batch[1]
    assert isinstance(raw, PlaceRawBuffer)
    assert len(raw.data) == W * H * 4


# ── the contract survives serialization (socket path) ─────────────────────────

def test_command_wire_roundtrip():
    batch = paint_scene(parse_scene(SCENE))
    again = [from_wire(to_wire(c)) for c in batch]
    assert [type(a) is type(b) for a, b in zip(batch, again)] == [True] * len(batch)
    # bitmap bytes preserved exactly through base64
    orig = next(c for c in batch if isinstance(c, PlaceRawBuffer))
    rt = next(c for c in again if isinstance(c, PlaceRawBuffer))
    assert rt.data == orig.data


# ── full stack render ─────────────────────────────────────────────────────────

def test_end_to_end_html_render(service):
    Compositor(InProcessTarget(service)).render_html(SCENE)
    service.render_once()
    frame = service.backend.snapshot_rgba()
    assert frame.shape == (H, W, 4)
    # background colour present where no other layer covers
    assert tuple(frame[H - 5, W - 5][:3]) == (20, 30, 60)
    # translucent status box darkened the background under it
    assert frame[45, 60][2] < 60


def test_alpha_blend_math(service):
    bg = raw_layer("bg", solid(W, H, (20, 30, 60, 255)), z=0)
    card = raw_layer("card", solid(W, H, (0, 0, 0, 170)), z=10)  # ~0.667 black
    frame = render(service, bg + card)
    # out = src*a + dst*(1-a) = 0*0.667 + 60*0.333 ≈ 19 on the blue channel
    assert tuple(int(v) for v in frame[60, 100][:3]) == (6, 9, 19)


def test_z_order_top_wins(service):
    blue = raw_layer("blue", solid(W, H, (0, 0, 255, 255)), z=0)
    red = raw_layer("red", solid(W, H, (255, 0, 0, 255)), z=10)
    # submit red-first to prove z (not submit order) decides stacking
    frame = render(service, red + blue)
    assert tuple(frame[60, 100][:3]) == (255, 0, 0)


def test_hide_and_show_layer(service):
    bg = raw_layer("bg", solid(W, H, (20, 30, 60, 255)), z=0)
    fg = raw_layer("fg", solid(W, H, (200, 0, 0, 255)), z=10)
    assert tuple(render(service, bg + fg)[60, 100][:3]) == (200, 0, 0)
    assert tuple(render(service, [HideLayer("fg")])[60, 100][:3]) == (20, 30, 60)
    assert tuple(render(service, [ShowLayer("fg")])[60, 100][:3]) == (200, 0, 0)


# ── the one invariant the whole stack depends on ──────────────────────────────

def test_rgba_to_bgra_boundary(service):
    """RGBA above, BGRA at the hardware. Pure-red RGBA must hit the fb as BGRA."""
    render(service, raw_layer("r", solid(W, H, (255, 0, 0, 255))))
    fb_bgra = service.backend.screen.copy()        # what drm_display received
    assert tuple(fb_bgra[60, 100]) == (0, 0, 255, 255)


def test_partial_buffer_clips_to_layer(service):
    # 1x1 red pixel placed at the bottom-right corner — must not error or wrap
    px = solid(1, 1, (255, 0, 0, 255))
    batch = [CreateLayer("p", W, H, z=0),
             PlaceRawBuffer(name="p", width=1, height=1,
                            data=px.tobytes(), x=W - 1, y=H - 1)]
    frame = render(service, batch)
    assert tuple(frame[H - 1, W - 1][:3]) == (255, 0, 0)
    assert tuple(frame[0, 0]) == (0, 0, 0, 0)       # rest untouched


# ── the threaded service path (production shape) ──────────────────────────────

def test_threaded_service_renders(backend):
    service = ScreenService(backend, fps=60)
    service.start()
    try:
        target = InProcessTarget(service)
        target.submit(raw_layer("bg", solid(W, H, (10, 20, 30, 255))))
        deadline = time.monotonic() + 2.0
        frame = None
        while time.monotonic() < deadline:
            frame = backend.snapshot_rgba()
            if frame is not None and tuple(frame[60, 100][:3]) == (10, 20, 30):
                break
            time.sleep(0.01)
        assert frame is not None
        assert tuple(frame[60, 100][:3]) == (10, 20, 30)
    finally:
        service.stop()
