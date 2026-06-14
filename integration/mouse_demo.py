#!/usr/bin/env python3
"""Interactive pointer demo — touch or mouse, on the real display.

Shows the full input path: drm_touch reads the pointer (a touchscreen if you
have one, else your mouse), drm_screen answers hit_test(), and the app reacts —
highlighting buttons, updating a label — while an autonomous cursor overlay
tracks the pointer smoothly.

    .venv/bin/python integration/mouse_demo.py                 # auto: touch -> mouse
    .venv/bin/python integration/mouse_demo.py --source mouse  # force mouse
    .venv/bin/python integration/mouse_demo.py --selftest      # headless, scripted

Real run needs: a usable display (DRM master — a text console), and read access
to /dev/input/event* (the `input` group, or sudo). Click "Quit" or Ctrl+C.
"""

import argparse
import os
import queue
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import (
    CreateLayer, PlaceRawBuffer, DeleteLayer, SetInteractive,
)
from drm_touch import find_pointer_source, fan_out, TouchReader, DummyTouch, TouchEvent

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mouse_frame.png")


def _font(sz):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", sz)
    except OSError:
        return ImageFont.load_default()


def button_bitmap(w, h, label, rgb, pressed=False):
    shade = 1.3 if pressed else 1.0
    fill = tuple(min(255, int(c * shade)) for c in rgb) + (255,)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=fill)
    f = _font(28)
    tw = d.textlength(label, font=f)
    d.text(((w - tw) / 2, h / 2 - 18), label, fill=(255, 255, 255, 255), font=f)
    return np.asarray(img, dtype=np.uint8)


def text_bitmap(w, h, text, size=26, rgb=(220, 230, 240)):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), text, fill=rgb + (255,), font=_font(size))
    return np.asarray(img, dtype=np.uint8)


def raw(name, rgba, x, y):
    h, w = rgba.shape[:2]
    return PlaceRawBuffer(name=name, width=w, height=h,
                          data=np.ascontiguousarray(rgba).tobytes(), x=x, y=y)


class Demo:
    def __init__(self, service):
        self.service = service
        self.W, self.H = service.backend.width, service.backend.height
        self.running = True
        self.hello_count = 0
        self.clicks = []          # (x, y, hit) for every 'down' — diagnostics
        # buttons: id -> geometry + look
        cx = self.W // 2
        self.buttons = {
            "hello": dict(x=cx - 320, y=self.H // 2 - 45, w=280, h=90,
                          label="Say Hello", rgb=(40, 90, 160)),
            "quit": dict(x=cx + 40, y=self.H // 2 - 45, w=280, h=90,
                         label="Quit", rgb=(160, 50, 50)),
        }

    def build(self):
        W, H = self.W, self.H
        batch = [CreateLayer("bg", W, H, z=0)]
        batch.append(raw("bg", _solid(W, H, (18, 22, 34)), 0, 0))
        batch += self._status_cmds("Tap a button (or click with the mouse).")
        for bid, b in self.buttons.items():
            batch.append(CreateLayer(bid, b["w"], b["h"], x=b["x"], y=b["y"], z=10,
                                     interactive=True, hit_id=bid))
            # layer is already positioned at (b.x, b.y); blit at the layer origin
            batch.append(raw(bid, button_bitmap(b["w"], b["h"], b["label"], b["rgb"]),
                             0, 0))
        self.service.submit(batch)

    def _status_cmds(self, text):
        W = self.W
        return [CreateLayer("status", W, 40, x=0, y=self.H - 70, z=5),
                PlaceRawBuffer(name="status", width=W - 40, height=40,
                               data=np.ascontiguousarray(
                                   text_bitmap(W - 40, 40, text)).tobytes(),
                               x=40, y=0)]

    def press(self, bid, down):
        b = self.buttons[bid]
        self.service.submit([raw(bid, button_bitmap(b["w"], b["h"], b["label"],
                                                     b["rgb"], pressed=down),
                                 0, 0)])

    def on_event(self, ev):
        if ev.phase == "down":
            hit = self.service.hit_test(ev.x, ev.y)
            self.clicks.append((ev.x, ev.y, hit))
            if hit:
                self.press(hit, True)
                if hit == "hello":
                    self.hello_count += 1
                    self.service.submit(self._status_cmds(
                        f"Hello! (x{self.hello_count})"))
                elif hit == "quit":
                    self.running = False
        elif ev.phase == "up":
            for bid in self.buttons:
                self.press(bid, False)


def _solid(w, h, rgb):
    a = np.empty((h, w, 4), dtype=np.uint8)
    a[:] = (*rgb, 255)
    return a


def run_real(args):
    backend = DrmDisplayBackend(device=args.device)
    service = ScreenService(backend, fps=60)
    demo = Demo(service)
    service.start()
    demo.build()

    app_q: queue.Queue = queue.Queue()
    source = find_pointer_source(demo.W, demo.H, prefer=args.source)
    print(f"input source: {type(source).__name__}")
    reader = TouchReader(source, fan_out(service.submit, app_q))
    reader.start()

    try:
        while demo.running:
            try:
                ev = app_q.get(timeout=0.1)
            except queue.Empty:
                continue
            demo.on_event(ev)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        backend.screen.clear()
        service.stop()
    # diagnostics, printed after the display closes
    print(f"\nscreen {demo.W}x{demo.H}; buttons:")
    for bid, b in demo.buttons.items():
        print(f"  {bid:6} x[{b['x']},{b['x'] + b['w']}) y[{b['y']},{b['y'] + b['h']})")
    print(f"clicks seen: {len(demo.clicks)}")
    for x, y, hit in demo.clicks[-12:]:
        print(f"  down ({x},{y}) -> hit={hit}")
    return 0


def run_selftest():
    backend = DrmDisplayBackend(device="dummy", width=800, height=480)
    service = ScreenService(backend)
    demo = Demo(service)
    demo.build()
    service.render_once()

    app_q: queue.Queue = queue.Queue()
    sink = fan_out(service.submit, app_q)
    b = demo.buttons["hello"]
    hx, hy = b["x"] + b["w"] // 2, b["y"] + b["h"] // 2
    q = demo.buttons["quit"]
    qx, qy = q["x"] + q["w"] // 2, q["y"] + q["h"] // 2
    script = [TouchEvent("hover", hx, hy), TouchEvent("down", hx, hy),
              TouchEvent("up", hx, hy), TouchEvent("down", qx, qy)]

    for ev in script:
        sink(ev)                       # cursor + app queue
        while not app_q.empty():
            demo.on_event(app_q.get_nowait())
        service.render_once()

    assert demo.hello_count == 1, demo.hello_count
    assert demo.running is False, "Quit press did not stop the loop"
    Image.fromarray(backend.snapshot_rgba(), "RGBA").save(OUT)
    print(f"selftest OK (hello x{demo.hello_count}, quit handled) -> {OUT}")
    service.stop()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None, help="display backend (default: auto)")
    ap.add_argument("--source", default=None,
                    choices=["touch", "composite", "abs", "mouse", "dummy"],
                    help="force input source (composite = VMware split abs+rel)")
    ap.add_argument("--selftest", action="store_true",
                    help="headless scripted run (no hardware)")
    args = ap.parse_args()
    return run_selftest() if args.selftest else run_real(args)


if __name__ == "__main__":
    sys.exit(main())
