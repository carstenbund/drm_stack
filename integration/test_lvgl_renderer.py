"""Stage 4b — the same stack, drawing through LVGL instead of numpy.

Two things are worth proving together, which is why they are here rather than
in either package alone:

1. A batch built by `drm_composer` for the RGBA compositor reaches an LVGL
   screen unchanged and puts the same picture on it. The command records are
   the contract; the compositor is an implementation detail.
2. A layer of `<path>` elements -- which the RGBA compositor cannot draw at all,
   and says so -- draws itself on a renderer that declares the `scene`
   capability.

Skipped where the plugin is not installed, or is installed without its native
library: a renderer plugin is by definition something the stack runs without.
"""

import numpy as np
import pytest

from drm_composer import Compositor, paint_scene, parse_scene
from drm_screen import InProcessTarget, ScreenService
from drm_screen.commands import UnsupportedCommand
from drm_screen.renderers import available

from conftest import H, W, solid

pytestmark = pytest.mark.skipif(
    "lvgl" not in available(),
    reason="drm_screen_lvgl not installed, or its native library is missing",
)

BOXES = f"""
<screen width="{W}" height="{H}">
  <layer id="bg" z="0"><box x="0" y="0" w="{W}" h="{H}" color="#101014" /></layer>
  <layer id="mark" z="10"><box x="20" y="20" w="40" h="30" color="#dc3c3c" /></layer>
</screen>
"""

STROKE = f"""
<screen width="{W}" height="{H}">
  <layer id="ink" z="10">
    <path id="rule" d="M 20 60 L 180 60" stroke="#e8e8f0" stroke-width="4" progress="0">
      <animate property="progress" from="0" to="1" start="0" duration="1000" />
    </path>
  </layer>
</screen>
"""


@pytest.fixture
def lvgl_service():
    service = ScreenService(
        renderer="lvgl",
        renderer_options=dict(width=W, height=H, display="memory"),
    )
    yield service
    service.stop()


def ink(frame) -> int:
    return int((frame[..., :3].max(axis=2) > 80).sum())


def test_a_composer_batch_draws_the_same_picture_on_either_renderer(service, lvgl_service):
    """`service` is the numpy compositor from conftest; same batch, both."""
    batch = paint_scene(parse_scene(BOXES))

    service.submit(batch)
    service.render_once()
    through_numpy = service.backend.snapshot_rgba()

    lvgl_service.submit(batch)
    lvgl_service.render_once()
    through_lvgl = lvgl_service.renderer.snapshot_rgba()

    np.testing.assert_array_equal(through_lvgl[..., :3], through_numpy[..., :3])


def test_the_pointer_overlay_still_belongs_to_the_screen(lvgl_service):
    from drm_screen.commands import SetPointer

    lvgl_service.submit([SetPointer(x=W // 2, y=H // 2)])
    lvgl_service.render_once()

    assert ink(lvgl_service.renderer.snapshot_rgba()) > 0


def test_hit_testing_survives_the_change_of_renderer(lvgl_service):
    from drm_screen.commands import CreateLayer, PlaceRawBuffer

    lvgl_service.submit([
        CreateLayer("ok", 40, 20, x=10, y=10, z=5, interactive=True, hit_id="ok"),
        PlaceRawBuffer("ok", 40, 20, data=solid(40, 20, (48, 96, 160, 255)).tobytes()),
    ])
    lvgl_service.render_once()

    assert lvgl_service.hit_test(20, 15) == "ok"
    assert lvgl_service.hit_test(150, 100) is None


def test_a_stroke_draws_itself(lvgl_service):
    """The thing a bitmap layer cannot express: a line part way through being
    drawn. `progress` is evaluated against the scene time, every frame."""
    Compositor(InProcessTarget(lvgl_service)).render_html(STROKE)

    drawn = []
    for scene_time in (0.0, 250.0, 500.0, 1000.0):
        lvgl_service.render_once(scene_time)
        drawn.append(ink(lvgl_service.renderer.snapshot_rgba()))

    assert drawn[0] == 0, "nothing is drawn at the start"
    assert drawn == sorted(drawn), f"the stroke must only grow: {drawn}"
    assert drawn[-1] > drawn[1] * 2, f"and it must get most of the way: {drawn}"


def test_the_numpy_compositor_refuses_a_scene_rather_than_dropping_it(service):
    """The fallback that is not one: pixels cannot hold a half-drawn stroke, so
    the RGBA path says so instead of showing an empty layer."""
    batch = paint_scene(parse_scene(STROKE))

    with pytest.raises(UnsupportedCommand):
        for command in batch:
            service.renderer.apply(command)
