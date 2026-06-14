#!/usr/bin/env python3
"""Interactive on-screen demo — renders to the ACTUAL DRM display.

Steps through a series of visual scenes across the full stack
(drm_composer -> drm_screen -> drm_display).  Each step waits for you to press
Enter before it is shown, so you can eyeball the real panel.

    .venv/bin/python integration/screen_demo.py          # auto-detect real display
    .venv/bin/python integration/screen_demo.py --device /dev/dri/card0
    .venv/bin/python integration/screen_demo.py --device dummy --no-wait   # smoke test

Real-display notes:
  * You must be able to open /dev/dri/cardN — be in the `video` group or run
    via sudo.
  * If a compositor (X11/Wayland) holds the DRM master lock, writes are
    ignored.  Run from a text console (Ctrl+Alt+F3) or stop the compositor.
    `drm-list-modes` shows who holds master.
  * The most important check is step 1: a RED screen must look RED.  If it is
    BLUE, the RGBA->BGRA boundary is wrong.
"""

import argparse
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from drm_screen import DrmDisplayBackend, ScreenService, InProcessTarget
from drm_screen.commands import (
    CreateLayer, PlaceRawBuffer, DeleteLayer, HideLayer, ShowLayer, SetPosition,
)
from drm_composer import Compositor


# ── small drawing helpers (RGBA throughout) ───────────────────────────────────

def _font(size):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def solid(w, h, rgba):
    buf = np.empty((h, w, 4), dtype=np.uint8)
    buf[:] = np.asarray(rgba, dtype=np.uint8)
    return buf


def labelled(w, h, rgba, text, fg=(255, 255, 255, 255), size=48):
    img = Image.fromarray(solid(w, h, rgba), "RGBA")
    d = ImageDraw.Draw(img)
    d.text((24, h // 2 - size // 2), text, fill=fg, font=_font(size))
    return np.asarray(img, dtype=np.uint8)


def gradient(w, h):
    xs = np.linspace(0, 255, w, dtype=np.uint8)
    ys = np.linspace(0, 255, h, dtype=np.uint8)
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., 0] = xs[None, :]        # R ramps left->right
    img[..., 1] = ys[:, None]        # G ramps top->bottom
    img[..., 2] = 128
    img[..., 3] = 255
    return img


def raw_layer(name, rgba, W, H, z=0, x=0, y=0):
    h, w = rgba.shape[:2]
    return [
        CreateLayer(name, W, H, z=z),
        PlaceRawBuffer(name=name, width=w, height=h,
                       data=np.ascontiguousarray(rgba).tobytes(), x=x, y=y),
    ]


# ── the demo ──────────────────────────────────────────────────────────────────

class Demo:
    def __init__(self, service, interactive=True):
        self.service = service
        self.target = InProcessTarget(service)
        self.composer = Compositor(self.target)
        self.W = service.backend.width
        self.H = service.backend.height
        self.interactive = interactive
        self.step_no = 0

    def pause(self, prompt):
        if self.interactive:
            try:
                input(f"    >> {prompt} (Enter) ")
            except EOFError:
                self.interactive = False
        else:
            print(f"    >> {prompt} (auto)")

    def show(self, batch):
        self.target.submit(batch)
        self.service.render_once()

    def reset(self, *names):
        self.show([DeleteLayer(n) for n in names])

    def step(self, title, look_for):
        self.step_no += 1
        print(f"\n[{self.step_no}] {title}")
        print(f"    look for: {look_for}")
        self.pause("show it")

    # ── individual scenes ─────────────────────────────────────────────────────

    def run(self):
        W, H = self.W, self.H
        print(f"\nDisplay {W}x{H} — stepping through the stack.\n")

        self.step("Solid RED fill",
                  "the WHOLE screen is red. If it's BLUE, RGBA->BGRA is wrong.")
        self.show(raw_layer("c", solid(W, H, (220, 30, 30, 255)), W, H))

        self.step("R / G / B bars (channel-order check)",
                  "three vertical bars, labelled, in the right colours.")
        bw = W // 3
        self.show(
            raw_layer("r", labelled(bw, H, (220, 30, 30, 255), "RED"), W, H, z=1, x=0)
            + raw_layer("g", labelled(bw, H, (30, 200, 30, 255), "GREEN"), W, H, z=1, x=bw)
            + raw_layer("b", labelled(W - 2 * bw, H, (40, 80, 230, 255), "BLUE"), W, H, z=1, x=2 * bw)
        )
        self.reset("c", "r", "g", "b")

        self.step("Gradient",
                  "a smooth red(L->R) / green(T->B) gradient, no banding/tearing.")
        self.show(raw_layer("grad", gradient(W, H), W, H))
        self.reset("grad")

        self.step("HTML scene via drm_composer",
                  "dark-blue background, translucent status card, white text.")
        self.composer.render_html(f"""
          <screen width="{W}" height="{H}">
            <layer id="bg" z="0"><box x="0" y="0" w="{W}" h="{H}" color="#141e3c"/></layer>
            <layer id="card" z="10">
              <box x="{W//12}" y="{H//6}" w="{W*5//8}" h="{H//4}" color="#000000aa"/>
              <text x="{W//12+30}" y="{H//6+30}" size="40" color="#ffffff">System ready</text>
            </layer>
          </screen>""")

        self.step("Alpha overlay",
                  "the whole scene dims under a 60% black wash (card still visible).")
        self.show(raw_layer("dim", solid(W, H, (0, 0, 0, 150)), W, H, z=50))
        self.reset("dim")

        self.step("Z-order",
                  "a BLUE box drawn on TOP of a RED box (blue wins the overlap).")
        self.show(
            raw_layer("red", solid(W // 2, H // 2, (220, 30, 30, 255)), W, H, z=20, x=W//6, y=H//6)
            + raw_layer("blue", solid(W // 2, H // 2, (40, 80, 230, 255)), W, H, z=30, x=W//3, y=H//3)
        )

        self.step("Hide the blue box", "the blue box disappears; red shows through.")
        self.show([HideLayer("blue")])
        self.step("Show it again", "the blue box returns on top.")
        self.show([ShowLayer("blue")])
        self.reset("bg", "card", "red", "blue")

        self.step("Animated move (partial updates)",
                  "a box sweeps left->right smoothly.")
        self.show(raw_layer("mv", solid(120, 120, (250, 200, 0, 255)), W, H, z=5, x=0, y=H//2-60))
        frames = 60 if self.interactive else 4
        for i in range(frames + 1):
            x = int((W - 120) * i / frames)
            self.show([SetPosition("mv", x, H // 2 - 60)])
            if self.interactive:
                time.sleep(0.016)
        self.reset("mv")

        self.step("Clear to black", "the screen goes black — demo complete.")
        self.service.backend.screen.clear()
        print("\nDone.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default=None,
                    help="DRM device, /dev/fb0, or 'dummy' (default: auto-detect)")
    ap.add_argument("--width", type=int, default=None, help="force width (no-EDID panels)")
    ap.add_argument("--height", type=int, default=None, help="force height")
    ap.add_argument("--no-wait", action="store_true",
                    help="do not wait for Enter between steps (smoke test)")
    args = ap.parse_args()

    backend = DrmDisplayBackend(device=args.device, width=args.width, height=args.height)
    service = ScreenService(backend, fps=60)
    demo = Demo(service, interactive=not args.no_wait)
    try:
        demo.run()
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        try:
            backend.screen.clear()
        except Exception:
            pass
        backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
