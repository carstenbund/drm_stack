#!/usr/bin/env python3
"""drm_stack integration demo — exercises all three packages together.

  screen-HTML
     -> drm_composer  (parse + rasterize -> command batch)
     -> drm_screen    (layers + composite -> RGBA frame)
     -> drm_display    (frame -> pixels;  here: headless dummy backend)

Runs headless so it never touches a real display.  Asserts the pipeline end to
end and saves the composited frame.  Exit 0 = the stack is wired correctly.
"""

import os
import sys
import time

from PIL import Image

import drm_display          # noqa: F401  (proves the bottom of the stack imports)
from drm_screen import DrmDisplayBackend, ScreenService, InProcessTarget
from drm_composer import Compositor

W, H = 800, 480
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stack_frame.png")

SCENE = """
<screen width="800" height="480">
  <layer id="background" z="0">
    <box x="0" y="0" w="800" h="480" color="#141e3c" />
  </layer>
  <layer id="status" z="10">
    <box x="40" y="40" w="380" h="96" color="#000000aa" />
    <text x="60" y="74" size="24" color="#ffffff">drm_stack: System ready</text>
  </layer>
  <layer id="hint" z="20">
    <text x="60" y="430" size="18" color="#7fd0ff">composer -&gt; screen -&gt; display</text>
  </layer>
</screen>
"""


def main() -> int:
    # bottom of the stack: drm_screen wraps drm_display (forced headless = safe)
    backend = DrmDisplayBackend(device="dummy", width=W, height=H)
    service = ScreenService(backend, fps=30)
    service.start()

    # top of the stack: drm_composer compiles HTML and submits to the service
    compositor = Compositor(InProcessTarget(service))
    batch = compositor.render_html(SCENE)
    print(f"compiled {len(batch)} commands from screen-HTML")

    time.sleep(0.15)  # let the render thread composite

    frame = backend.snapshot_rgba()
    assert frame is not None and frame.shape == (H, W, 4), "no frame rendered"
    assert tuple(frame[300, 600][:3]) == (20, 30, 60), "background layer missing"
    assert frame[100, 200][2] < 60, "status layer did not blend over background"

    Image.fromarray(frame, "RGBA").save(OUT)
    service.stop()
    print(f"OK — stack wired end to end; frame -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
