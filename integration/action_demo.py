#!/usr/bin/env python3
"""Action demo — buttons that run commands, with the allowlist enforced.

`<button action="X">` compiles to an interactive layer with hit_id `cmd:X`.
`drm_composer.parse_action` turns that into `Action(kind="command", target="X")`,
and the host's `Dispatcher` runs it — but ONLY if the host registered a handler
for that name. Unregistered commands are a silent no-op (the security allowlist):
the "Wipe" button below emits `cmd:wipe`, which is deliberately NOT registered,
so it does nothing — `drm_composer` never executes anything itself.

    .venv/bin/python integration/action_demo.py            # real display + pointer
    .venv/bin/python integration/action_demo.py --selftest  # headless, scripted
"""

import argparse
import os
import queue
import sys

from PIL import Image

from drm_screen import DrmDisplayBackend, ScreenService
from drm_composer import Compositor, parse_action, Dispatcher
from drm_touch import find_pointer_source, fan_out, TouchReader, TouchEvent

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "action_frame.png")


class _Denials:
    """A logger the Dispatcher calls when a hit has no registered handler."""
    def __init__(self, app):
        self.app = app

    def info(self, fmt, *args):
        self.app.note("denied (not allowlisted): " + (str(args[0]) if args else fmt))


class ActionApp:
    def __init__(self, service):
        self.service = service
        self.W, self.H = service.backend.width, service.backend.height
        self.compositor = Compositor(target=None)
        self.running = True
        self.count = 0
        self.on = False
        self.last = "tap a command — registered ones run, others are denied"

        # The allowlist: only these commands can fire. cmd:wipe is NOT here.
        self.dispatch = (Dispatcher(logger=_Denials(self))
                         .on_command("hello", self._hello)
                         .on_command("count", self._count)
                         .on_command("toggle", self._toggle)
                         .on_action("quit", self._quit))

    # ── command handlers (the host is the sole executor) ──────────────────────

    def _hello(self):
        self.note("hello! cmd:hello ran")

    def _count(self):
        self.count += 1
        self.note(f"cmd:count ran — count = {self.count}")

    def _toggle(self):
        self.on = not self.on
        self.note(f"cmd:toggle ran — state = {'ON' if self.on else 'OFF'}")

    def _quit(self):
        self.running = False

    def note(self, msg):
        self.last = msg
        self.service.submit(self.compositor.compile(self._status_scene()))  # update status only

    # ── rendering ─────────────────────────────────────────────────────────────

    def _status_scene(self):
        return (f'<screen width="{self.W}" height="{self.H}">'
                f'<layer id="status" z="5">'
                f'<box x="5%" y="74%" w="90%" h="9%" color="#0c1830"/>'
                f'<text x="7%" y="76%" size="24" color="#9ecbff">{self.last}</text>'
                f'</layer></screen>')

    def page(self):
        W, H = self.W, self.H
        def btn(action, label, x, y, color):
            return (f'<button action="{action}" x="{x}%" y="{y}%" w="40%" h="16%" '
                    f'size="26" color="{color}">{label}</button>')
        buttons = (
            btn("hello", "Hello", 8, 30, "#2a6cae")
            + btn("count", "Count ++", 52, 30, "#2a6cae")
            + btn("toggle", "Toggle", 8, 50, "#3a8a55")
            + btn("wipe", "Wipe &#9888; (denied)", 52, 50, "#a33a3a"))
        return f"""
        <screen width="{W}" height="{H}">
          <layer id="bg" z="0">
            <box x="0" y="0" w="100%" h="100%" color="#10141c"/>
            <text x="5%" y="6%" size="40" color="#ffffff">Action demo — buttons emit cmd:&lt;action&gt;</text>
            <text x="5%" y="15%" size="22" color="#7fb0d0">Registered commands run; unregistered ones are silently denied (the allowlist).</text>
            {buttons}
            <button id="quit" x="40%" y="86%" w="20%" h="11%" size="24" color="#555">Quit</button>
          </layer>
          {self._status_scene_layer()}
        </screen>"""

    def _status_scene_layer(self):
        # the status layer markup (without its own <screen> wrapper) for the page
        return (f'<layer id="status" z="5">'
                f'<box x="5%" y="74%" w="90%" h="9%" color="#0c1830"/>'
                f'<text x="7%" y="76%" size="24" color="#9ecbff">{self.last}</text>'
                f'</layer>')

    def load(self):
        self.service.submit(self.compositor.compile(self.page()))

    def on_event(self, ev):
        if ev.phase != "down":
            return
        self.dispatch.dispatch(parse_action(self.service.hit_test(ev.x, ev.y)))

    def button_center(self, hit_id):
        for layer in self.service.composer.layers.values():
            if layer.interactive and layer.hit_id == hit_id:
                return layer.x + layer.width // 2, layer.y + layer.height // 2
        return None


def run_real(args):
    backend = DrmDisplayBackend(device=args.device, width=args.width, height=args.height)
    service = ScreenService(backend, fps=60)
    app = ActionApp(service)
    service.start()
    app.load()

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
    app = ActionApp(service)
    app.load()
    service.render_once()

    app_q: queue.Queue = queue.Queue()
    sink = fan_out(service.submit, app_q)

    def tap(hit_id):
        c = app.button_center(hit_id)
        assert c is not None, f"{hit_id} not on screen"
        sink(TouchEvent("down", *c))
        while not app_q.empty():
            app.on_event(app_q.get_nowait())
        service.render_once()

    tap("cmd:hello");  assert "hello" in app.last
    tap("cmd:count");  assert app.count == 1, app.count
    tap("cmd:count");  assert app.count == 2, app.count
    tap("cmd:toggle"); assert app.on is True
    # the denied command: registered nowhere -> no state change, noted as denied
    before = (app.count, app.on)
    tap("cmd:wipe")
    assert (app.count, app.on) == before, "denied command changed state!"
    assert "denied" in app.last and "cmd:wipe" in app.last, app.last
    Image.fromarray(backend.snapshot_rgba(), "RGBA").save(OUT)
    tap("quit");       assert app.running is False

    print(f"selftest OK — allowed commands ran, cmd:wipe denied -> {OUT}")
    service.stop()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None, help="display backend (default: auto)")
    ap.add_argument("--width", type=int, default=None, help="force display width")
    ap.add_argument("--height", type=int, default=None, help="force display height")
    ap.add_argument("--source", default=None,
                    choices=["touch", "composite", "abs", "mouse", "dummy"])
    ap.add_argument("--selftest", action="store_true", help="headless scripted run")
    args = ap.parse_args()
    return run_selftest() if args.selftest else run_real(args)


if __name__ == "__main__":
    sys.exit(main())
