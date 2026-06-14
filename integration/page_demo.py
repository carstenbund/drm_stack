#!/usr/bin/env python3
"""Full-chain demo app — HTML pages, navigation, images, touch/mouse input.

Exercises the entire stack as designed:

    screen-HTML page ─▶ drm_composer ─▶ drm_screen (service thread) ─▶ drm_display
                                              ▲
    drm_touch ─▶ app queue ─▶ hit_test() ─────┘   (Next / Back / links)

Pages are laid out in **percentages**, so the *same HTML* fits any resolution —
only the `<screen>` tag is stamped with the runtime display size. A page history
stack gives `goto` / `back`. One home screen offers a text **slideshow**, a
**photo** slideshow (`<img>`), and a branching **app** (menu → sub-pages → back).

Photos: drop image1.jpg … image5.jpg into integration/images/ (a missing image
shows a placeholder — drm_composer treats <img> leniently).

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
from drm_composer import Compositor, parse_action, Dispatcher
from drm_touch import find_pointer_source, fan_out, TouchReader, TouchEvent

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_frame.png")
IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
N_PHOTOS = 5


# ── pages: name -> builder(W, H) -> screen-HTML (W,H only fill <screen>) ───────

def _bar(back=False, link=None):
    """Bottom bar: optional Back <button> (action) + right-hand <a> (link)."""
    out = ""
    if back:
        out += ('<button id="back" x="3%" y="83%" w="24%" h="12%" '
                'color="#444c5c">&#9664; Back</button>')
    if link:
        href, label, color = link
        out += (f'<a href="{href}" x="73%" y="83%" w="24%" h="12%" '
                f'color="{color}">{label}</a>')
    return out


def page_home(W, H):
    items = [("slide1.html", "Slideshow", "#2a6cae"),
             ("imgslide1.html", "Photos", "#8a6d3b"),
             ("menu.html", "App demo", "#3a8a55")]
    links = "".join(
        f'<a href="{href}" x="{4 + k * 32}%" y="40%" w="28%" h="18%" '
        f'color="{color}">{label} &#9654;</a>'
        for k, (href, label, color) in enumerate(items))
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="100%" h="100%" color="#0e1422"/>
        <text x="5%" y="8%" size="46" color="#ffffff">drm_stack — full-chain demo</text>
        <text x="5%" y="18%" size="22" color="#7fb0d0">Laid out in %, so the same HTML fits any resolution.</text>
        {links}
        <button id="quit" x="35%" y="66%" w="30%" h="14%" color="#a33a3a">Quit</button>
      </layer>
    </screen>"""


def _slide(W, H, n, total, title, body, nxt):
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="100%" h="100%" color="#101826"/>
        <text x="88%" y="5%" size="22" color="#5a708a">{n} / {total}</text>
        <text x="5%" y="11%" size="44" color="#ffffff">{title}</text>
        <text x="5%" y="24%" size="26" color="#cdd6e0">{body}</text>
        {_bar(back=True, link=nxt)}
      </layer>
    </screen>"""


SLIDES = [
    ("Layers, not widgets", "drm_screen owns named RGBA layers and composites by z."),
    ("One coordinate boundary", "RGBA everywhere; a single RGBA->BGRA at the backend."),
    ("Resolution-independent", "This page is laid out in %, resolved against the screen."),
]


def page_slide(i):
    def build(W, H):
        title, body = SLIDES[i]
        last = i == len(SLIDES) - 1
        nxt = (("home.html", "Finish &#9654;", "#3a8a55") if last
               else (f"slide{i + 2}.html", "Next &#9654;", "#2a6cae"))
        return _slide(W, H, i + 1, len(SLIDES), title, body, nxt)
    return build


# Photo pages are built from two SEPARATE layers so fullscreen can re-render the
# content layer alone, leaving the chrome (controls) layer untouched on top.

def _photo_content(src, full):
    """The content layer — just the image, framed or fullscreen. z=0."""
    geom = ('x="0" y="0" w="100%" h="100%"' if full
            else 'x="2%" y="8%" w="96%" h="78%"')
    return (f'<layer id="content" z="0">'
            f'<box x="0" y="0" w="100%" h="100%" color="#0b0d12"/>'
            f'<img src="{src}" {geom} fit="contain" fullscreen/>'
            f'</layer>')


def _photo_controls(n):
    """The chrome layer — caption + Fullscreen toggle + Back/Next. z=10 (on top)."""
    last = n == N_PHOTOS
    href, label, color = (("home.html", "Finish &#9654;", "#3a8a55") if last
                          else (f"imgslide{n + 1}.html", "Next &#9654;", "#8a6d3b"))
    return (f'<layer id="controls" z="10">'
            f'<text x="3%" y="3%" size="18" color="#7c8896">Photo {n} / {N_PHOTOS} — tap image to toggle fullscreen</text>'
            f'<button id="fs" x="80%" y="2%" w="17%" h="7%" size="18" color="#2a3a4a">Fullscreen</button>'
            f'<button id="back" x="2%" y="89%" w="15%" h="9%" size="20" color="#444c5c">&#9664; Back</button>'
            f'<a href="{href}" x="83%" y="89%" w="15%" h="9%" size="20" color="{color}">{label}</a>'
            f'</layer>')


def _photo_page(n, full, W, H):
    src = os.path.join(IMG_DIR, f"image{n}.jpg")
    return f'<screen width="{W}" height="{H}">{_photo_content(src, full)}{_photo_controls(n)}</screen>'


def page_menu(W, H):
    items = [("settings.html", "Settings"), ("about.html", "About")]


def page_menu(W, H):
    items = [("settings.html", "Settings"), ("about.html", "About")]
    btns = "".join(
        f'<a href="{href}" x="30%" y="{28 + k * 20}%" w="40%" h="15%" '
        f'color="#3a6ea5">{label}</a>'
        for k, (href, label) in enumerate(items))
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="100%" h="100%" color="#111a14"/>
        <text x="5%" y="8%" size="40" color="#ffffff">App menu</text>
        {btns}
        {_bar(back=True)}
      </layer>
    </screen>"""


def _content(W, H, title, body):
    return f"""
    <screen width="{W}" height="{H}">
      <layer id="bg" z="0">
        <box x="0" y="0" w="100%" h="100%" color="#161616"/>
        <text x="5%" y="9%" size="40" color="#ffffff">{title}</text>
        <text x="5%" y="24%" size="26" color="#cdd6e0">{body}</text>
        {_bar(back=True)}
      </layer>
    </screen>"""


PAGES = {
    "home": page_home,
    "slide1": page_slide(0), "slide2": page_slide(1), "slide3": page_slide(2),
    "menu": page_menu,
    "settings": lambda W, H: _content(W, H, "Settings", "Back restores the menu (history pop)."),
    "about": lambda W, H: _content(W, H, "About", "Built entirely from the drm-stack."),
    # imgslide{n} pages are built dynamically by PageApp (they depend on the
    # persistent fullscreen flag) — see PageApp._render.
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
        self.fullscreen = False          # persistent ("global") photo fullscreen

        # Composer emits hit_ids; the app is the executor. Registration is the
        # allowlist — only these handlers can fire (unknown hits are no-ops).
        self.dispatch = (Dispatcher()
                         .on_navigate(lambda target: self.goto(self._page_name(target)))
                         .on_fullscreen(lambda src: self.toggle_fullscreen())
                         .on_action("fs", self.toggle_fullscreen)
                         .on_action("back", self.back)
                         .on_action("quit", self._quit))

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
        self._load(self._render(name))

    def _render(self, name):
        if name.startswith("imgslide"):     # photo page depends on fullscreen state
            n = int(name[len("imgslide"):])
            return _photo_page(n, self.fullscreen, self.W, self.H)
        return PAGES[name](self.W, self.H)

    def back(self):
        if self.history:
            self.goto(self.history.pop(), push=False)

    def toggle_fullscreen(self):
        """Expand/shrink the photo by re-rendering ONLY the content layer — the
        controls layer (caption, Back/Next, the toggle) is never touched."""
        if not (self.current or "").startswith("imgslide"):
            return
        self.fullscreen = not self.fullscreen
        n = int(self.current[len("imgslide"):])
        src = os.path.join(IMG_DIR, f"image{n}.jpg")
        html = f'<screen width="{self.W}" height="{self.H}">{_photo_content(src, self.fullscreen)}</screen>'
        self.service.submit(self.compositor.compile(html))   # updates "content" in place

    def _quit(self):
        self.running = False

    @staticmethod
    def _page_name(href):
        return href[:-5] if href.endswith(".html") else href

    def on_event(self, ev):
        if ev.phase != "down":
            return
        self.dispatch.dispatch(parse_action(self.service.hit_test(ev.x, ev.y)))

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
    tap("href:menu.html");     assert app.current == "menu", app.current
    tap("href:settings.html"); assert app.current == "settings", app.current
    tap("back");               assert app.current == "menu", app.current
    tap("back");               assert app.current == "home", app.current
    # text slideshow
    tap("href:slide1.html");   tap("href:slide2.html"); assert app.current == "slide2"
    tap("back");               assert app.current == "slide1", app.current
    # photo slideshow (placeholders until images are supplied)
    tap("back");                assert app.current == "home", app.current
    tap("href:imgslide1.html"); assert app.current == "imgslide1", app.current
    tap("href:imgslide2.html"); assert app.current == "imgslide2", app.current
    tap("back");                assert app.current == "imgslide1", app.current
    # fullscreen TOGGLE: tap the image -> content expands, controls stay; tap fs -> shrink
    assert not app.fullscreen
    tap("full:" + os.path.join(IMG_DIR, "image1.jpg"))
    assert app.fullscreen, "image tap did not expand"
    # controls layer + buttons survived the content-only re-render
    assert "controls" in app.service.composer.layers
    assert "back" in app.service.composer.layers and "fs" in app.service.composer.layers
    Image.fromarray(backend.snapshot_rgba(), "RGBA").save(OUT)
    tap("fs");                  assert not app.fullscreen, "fs toggle did not shrink"
    # fullscreen persists across photos: turn on, go Next, still fullscreen
    tap("full:" + os.path.join(IMG_DIR, "image1.jpg")); assert app.fullscreen
    tap("href:imgslide2.html"); assert app.current == "imgslide2" and app.fullscreen
    tap("fs");                  assert not app.fullscreen
    tap("back");                assert app.current == "imgslide1", app.current
    tap("back");                assert app.current == "home", app.current
    tap("quit");                assert app.running is False

    print(f"selftest OK — slideshows + fullscreen toggle (chrome kept) -> {OUT}")
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
