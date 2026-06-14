#!/usr/bin/env python3
"""Full-chain demo app — HTML pages, navigation, touch/mouse input.

Exercises the entire stack as designed:

    screen-HTML page ─▶ drm_composer ─▶ drm_screen (service thread) ─▶ drm_display
                                              ▲
    drm_touch ─▶ app queue ─▶ hit_test() ─────┘   (Next / Back / goto buttons)

The app keeps a page **history stack**: `goto` pushes, `back` restores the
previous page. Two flavours from one home screen:
  * a linear **slideshow** (Next / Back)
  * a branching **app** (menu -> sub-pages -> back)

    .venv/bin/python integration/page_demo.py            # real display + pointer
    .venv/bin/python integration/page_demo.py --selftest  # headless, scripted nav
"""

import argparse
import os
import queue
import sys

from PIL import Image

from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import CreateLayer, DeleteLayer
from drm_composer import Compositor
from drm_touch import find_pointer_source, fan_out, TouchReader, TouchEvent

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_frame.png")


# ── pages: name -> builder(W, H) -> screen-HTML ───────────────────────────────

def _bar(W, H, back=False, right=None):
    """Bottom button bar: optional Back (left) and a right-hand action."""
    bw, bh, pad = 220, 64, 30
    out = ""
    if back:
        out += (f'<button id="back" x="{pad}" y="{H - bh - pad}" w="{bw}" h="{bh}" '
                f'color="#444c5c">&#9664; Back</button>')
    if right:
        rid, label, color = right
        out += (f'<button id="{rid}" x="{W - bw - pad}" y="{H - bh - pad}" '
                f'w="{bw}" h="{bh}" color="{color}">{label}</button>')
    return out


def page_home(W, H):
    cx, bw, bh, gap = W // 2, 300, 96, 28
    y = H // 2 - bh
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="{W}" h="{H}" color="#0e1422"/>
        <text x="60" y="56" size="46" color="#ffffff">drm_stack — full-chain demo</text>
        <text x="60" y="124" size="22" color="#7fb0d0">HTML page &#8594; composer &#8594; screen &#8594; display, with pointer input</text>
        <button id="goto:slide1" x="{cx - bw - gap // 2}" y="{y}" w="{bw}" h="{bh}" color="#2a6cae">Slideshow &#9654;</button>
        <button id="goto:menu" x="{cx + gap // 2}" y="{y}" w="{bw}" h="{bh}" color="#3a8a55">App demo &#9654;</button>
        <button id="quit" x="{cx - 110}" y="{y + bh + gap}" w="220" h="64" color="#a33a3a">Quit</button>
      </layer>
    </screen>"""


def _slide(W, H, n, total, title, body, nxt):
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="{W}" h="{H}" color="#101826"/>
        <text x="{W - 150}" y="40" size="22" color="#5a708a">{n} / {total}</text>
        <text x="60" y="80" size="44" color="#ffffff">{title}</text>
        <text x="60" y="170" size="26" color="#cdd6e0">{body}</text>
        {_bar(W, H, back=True, right=nxt)}
      </layer>
    </screen>"""


SLIDES = [
    ("Layers, not widgets", "drm_screen owns named RGBA layers and composites by z."),
    ("One coordinate boundary", "RGBA everywhere; a single RGBA->BGRA at the backend."),
    ("Input mirrors output", "drm_touch -> hit_test() -> the app submits the next page."),
]


def page_slide(i):
    def build(W, H):
        title, body = SLIDES[i]
        last = i == len(SLIDES) - 1
        nxt = (("goto:home", "Finish &#9654;", "#3a8a55") if last
               else (f"goto:slide{i + 2}", "Next &#9654;", "#2a6cae"))
        return _slide(W, H, i + 1, len(SLIDES), title, body, nxt)
    return build


def page_menu(W, H):
    bw, bh, gap = 360, 84, 24
    cx, y0 = W // 2 - bw // 2, H // 2 - bh - gap
    items = [("goto:settings", "Settings", "#3a6ea5"),
             ("goto:about", "About", "#3a6ea5")]
    btns = "".join(
        f'<button id="{bid}" x="{cx}" y="{y0 + k * (bh + gap)}" w="{bw}" h="{bh}" '
        f'color="{color}">{label}</button>'
        for k, (bid, label, color) in enumerate(items))
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="{W}" h="{H}" color="#111a14"/>
        <text x="60" y="60" size="40" color="#ffffff">App menu</text>
        {btns}
        {_bar(W, H, back=True)}
      </layer>
    </screen>"""


def _content(W, H, title, body):
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="{W}" h="{H}" color="#161616"/>
        <text x="60" y="70" size="40" color="#ffffff">{title}</text>
        <text x="60" y="160" size="26" color="#cdd6e0">{body}</text>
        {_bar(W, H, back=True)}
      </layer>
    </screen>"""


PAGES = {
    "home": page_home,
    "slide1": page_slide(0),
    "slide2": page_slide(1),
    "slide3": page_slide(2),
    "menu": page_menu,
    "settings": lambda W, H: _content(W, H, "Settings", "Back restores the menu (history pop)."),
    "about": lambda W, H: _content(W, H, "About", "Built entirely from the drm-stack."),
}


# ── the navigation app ────────────────────────────────────────────────────────

class PageApp:
    def __init__(self, service):
        self.service = service
        self.W, self.H = service.backend.width, service.backend.height
        self.compositor = Compositor(target=None)   # we only use .compile()
        self.history = []
        self.current = None
        self.current_layers = []
        self.running = True

    def _load(self, html):
        batch = self.compositor.compile(html)
        new_layers = [c.name for c in batch if isinstance(c, CreateLayer)]
        cmds = [DeleteLayer(n) for n in self.current_layers] + batch
        self.service.submit(cmds)          # one atomic batch: clear old, draw new
        self.current_layers = new_layers

    def goto(self, name, push=True):
        if push and self.current is not None:
            self.history.append(self.current)
        self.current = name
        self._load(PAGES[name](self.W, self.H))

    def back(self):
        if self.history:
            self.goto(self.history.pop(), push=False)

    def on_event(self, ev):
        if ev.phase != "down":
            return
        hit = self.service.hit_test(ev.x, ev.y)
        if not hit:
            return
        if hit == "quit":
            self.running = False
        elif hit == "back":
            self.back()
        elif hit.startswith("goto:"):
            self.goto(hit[len("goto:"):])

    def button_center(self, hit_id):
        for layer in self.service.composer.layers.values():
            if layer.interactive and layer.hit_id == hit_id:
                return layer.x + layer.width // 2, layer.y + layer.height // 2
        return None


# ── runners ───────────────────────────────────────────────────────────────────

def run_real(args):
    backend = DrmDisplayBackend(device=args.device)
    service = ScreenService(backend, fps=60)
    app = PageApp(service)
    service.start()
    app.goto("home")

    app_q: queue.Queue = queue.Queue()
    source = find_pointer_source(app.W, app.H, prefer=args.source)
    print(f"input source: {type(source).__name__}")
    reader = TouchReader(source, fan_out(service.submit, app_q))
    reader.start()
    try:
        while app.running:
            try:
                ev = app_q.get(timeout=0.1)
            except queue.Empty:
                continue
            app.on_event(ev)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        backend.screen.clear()
        service.stop()
    return 0


def run_selftest():
    backend = DrmDisplayBackend(device="dummy", width=1024, height=600)
    service = ScreenService(backend)
    app = PageApp(service)
    app.goto("home")
    service.render_once()

    app_q: queue.Queue = queue.Queue()
    sink = fan_out(service.submit, app_q)

    def tap(hit_id):
        c = app.button_center(hit_id)
        assert c is not None, f"button {hit_id!r} not on screen (page={app.current})"
        sink(TouchEvent("down", *c))
        while not app_q.empty():
            app.on_event(app_q.get_nowait())
        service.render_once()

    # branching app + back-restore
    tap("goto:menu");     assert app.current == "menu", app.current
    tap("goto:settings"); assert app.current == "settings", app.current
    tap("back");          assert app.current == "menu", app.current
    tap("back");          assert app.current == "home", app.current
    # slideshow chain
    tap("goto:slide1");   assert app.current == "slide1", app.current
    tap("goto:slide2");   assert app.current == "slide2", app.current
    tap("back");          assert app.current == "slide1", app.current
    tap("goto:slide2");   tap("goto:slide3"); assert app.current == "slide3", app.current
    tap("goto:home");     assert app.current == "home", app.current
    Image.fromarray(backend.snapshot_rgba(), "RGBA").save(OUT)
    tap("quit");          assert app.running is False

    print(f"selftest OK — navigation + back-restore verified -> {OUT}")
    service.stop()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None, help="display backend (default: auto)")
    ap.add_argument("--source", default=None,
                    choices=["touch", "composite", "abs", "mouse", "dummy"])
    ap.add_argument("--selftest", action="store_true", help="headless scripted run")
    args = ap.parse_args()
    return run_selftest() if args.selftest else run_real(args)


if __name__ == "__main__":
    sys.exit(main())
