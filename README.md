# drm_stack

A lightweight screen manager that receives declarative scenes, renders them
directly to a Linux display via DRM/KMS, and takes touch/mouse input — no X11,
Wayland, or browser engine.

This is the **umbrella repo**: stack-level docs, a dev bootstrap, and the
integration tests + demos.  The four packages live in their own repos (cloned in
by `setup.sh`); this repo never tracks their contents.

## The stack

**Output** — declarative scene to pixels:

```
Application
   ↓  declarative screen-HTML
drm_composer   scene markup → commands + RGBA bitmaps      (stateless compiler)
   ↓  command batch over submit() / socket
drm_screen     layers → composited RGBA frame             (stateful service)
   ↓  frame
drm_display    frame → DRM/KMS pixels                      (hardware backend)
```

**Input** — pointer to action (the mirror; the app is the hub):

```
drm_touch      touchscreen / mouse (evdev) → TouchEvents   (low-level input)
   ↓  app queue
Application    drm_screen.hit_test() query → app logic → submit()
   ↓
drm_screen     hit-testing + autonomous cursor overlay
```

| Package | Role | Owns | Never does | Repo |
|---|---|---|---|---|
| [`drm_composer`](https://github.com/carstenbund/drm_composer) | scene-to-command compiler | parse HTML, layout, rasterize to RGBA, emit commands | hold screen state, blend, touch DRM | drm_composer |
| [`drm_screen`](https://github.com/carstenbund/drm_screen) | screen manager / service | persistent layers, **compositing**, **hit-testing**, command API, render loop | parse HTML, touch DRM, hold app logic | drm_screen |
| [`drm_display`](https://github.com/carstenbund/drm_display) | low-level output | DRM/KMS, framebuffer, headless backends | anything above pixels | drm_display |
| [`drm_touch`](https://github.com/carstenbund/drm_touch) | low-level input | evdev touch/mouse, calibration, normalized `TouchEvent`s | hit-test, render, app logic | drm_touch |

`drm_display` is independently published (PyPI: `drm-display`, MIT); the others
are GPL-3.0-or-later and depend inward only.  `drm_touch` needs only `evdev` (plus
`drm_screen`'s command contract at runtime).

## Design invariants

These are the rules that keep the boundaries clean — break them and the stack
rots:

1. **One color-order boundary.** Everything from the composer down through the
   compositor is **RGBA**.  The single RGBA→BGRA conversion lives in
   `drm_screen`'s backend adapter, immediately before `drm_display.Screen.show()`
   (DRM framebuffers are BGRA / XRGB8888 little-endian).  Nothing else converts.

2. **Commands are data, not calls.** `drm_composer` emits serializable command
   records (`CreateLayer`, `PlaceRawBuffer`, …) with bitmaps as raw RGBA bytes.
   The same batch works whether enqueued in-process (debug) or sent over a socket
   (production).  This command surface is the **contract** between the two
   upper packages.

3. **One service, one async boundary.** `drm_screen` is the only long-running
   service: it owns the display thread, a command queue, and the render loop.
   `submit()` is the single non-blocking entry point — clients drop a batch and
   move on.  `drm_composer` is a **stateless synchronous utility**, not a service.

4. **Composition lives in `drm_screen`, not `drm_composer`.**  `drm_composer`
   compiles scenes; it never blends a final frame.  (Hence the deliberately
   distinct names: `drm_composer.Compositor` = scene compiler,
   `drm_screen.Composer` = pixel blender.)

5. **The blend is isolated.**  `drm_screen`'s `Composer.render()` is a single
   pure `layers → canvas` function.  If profiling ever demands native speed,
   that one function moves to Rust/PyO3 — nothing else changes.  (Decision:
   Python now, Rust-ready protocol; revisit only if sustained high fps or a
   single-binary embedded deployment becomes a hard requirement.)

6. **Input mirrors output; the app stays in control.**  `drm_touch` reads the
   pointer (touchscreen, else mouse — via evdev) and emits source-agnostic,
   screen-pixel `TouchEvent`s.  They flow to the **app**, which queries
   `drm_screen.hit_test()` and submits feedback — `drm_screen` holds no callbacks
   or app logic.  The single raw→pixel mapping lives in `drm_touch` (the input
   twin of the RGBA→BGRA boundary).  The cursor overlay is fed straight to the
   render queue, so it stays smooth regardless of app-loop latency (INT 33h-style).

## Quick start

```bash
git clone https://github.com/carstenbund/drm_stack
cd drm_stack
./setup.sh                          # clones the packages + editable-installs into .venv
source .venv/bin/activate
python integration/stack_demo.py    # headless end-to-end; writes integration/stack_frame.png
```

`setup.sh` is idempotent: existing package clones are left as-is, only missing
ones are cloned.  It installs the four packages in dependency order
(`drm_display → drm_screen → drm_touch → drm_composer`).

## Testing

The umbrella is where the packages are tested *together* — the boundaries no
single repo can cover (HTML→command contract, command→composite, the RGBA→BGRA
hardware boundary, and the input path: `drm_touch`→`hit_test`→cursor overlay).
All headless, no display or input hardware required.

```bash
make test                  # or: .venv/bin/pytest -q
```

Note: run the `pytest` console script (what `make test` does), **not**
`python -m pytest` from the repo root — `-m` puts the cwd on `sys.path`, where
the `drm_screen/` clone shadows the installed `drm_screen` package.

### On-screen demo (real display)

`make test` is headless.  To verify on an **actual panel**, step through a
visual demo — each scene waits for Enter:

```bash
make screen-demo                                          # auto-detect device
.venv/bin/python integration/screen_demo.py --device /dev/dri/card0
.venv/bin/python integration/screen_demo.py --device dummy --no-wait   # smoke test
```

It walks solid fill → R/G/B bars → gradient → HTML scene → alpha overlay →
z-order → hide/show → animated move → clear.  **Step 1 is the key check: a red
screen must look red** — if it's blue, the RGBA→BGRA boundary is broken.

Requirements:
- Permission to open `/dev/dri/cardN` — be in the `video` group (`sudo usermod
  -aG video $USER`, then re-login) or run via `sudo`.
- No compositor holding the DRM master lock.  If X11/Wayland is running, switch
  to a text console (Ctrl+Alt+F3) or stop the compositor; `drm-list-modes`
  reports who holds master.  Otherwise writes are silently ignored.

### Touch / mouse demo (real input)

Interactive buttons + a live cursor, driven by a touchscreen or, as a fallback,
your mouse:

```bash
make mouse-demo                                        # auto-detects the pointer
.venv/bin/python integration/mouse_demo.py --selftest  # headless, scripted (no hardware)
python -m drm_touch                                    # list input devices + show the pick
python -m drm_touch --watch                            # print live events (diagnostics)
```

Also needs the **`input`** group (`sudo usermod -aG input $USER`, then re-login)
to read `/dev/input/event*`.  `drm_touch` auto-detects: touchscreen → composite
(VMs that split motion/buttons) → absolute pointer → mouse.

## Layout

```
drm_stack/
  README.md                 # this file — the canonical stack overview
  setup.sh                  # clone + editable-install bootstrap
  Makefile                  # setup / test / demo / screen-demo / mouse-demo / clean
  pytest.ini                # integration test config
  integration/
    conftest.py             # headless fixtures (synchronous render)
    test_pipeline.py        # output-path integration tests
    test_input.py           # input-path integration tests (drm_touch → hit_test)
    stack_demo.py           # headless end-to-end demo (HTML → display)
    screen_demo.py          # interactive output demo (real display, Enter to step)
    mouse_demo.py           # interactive input demo (real display, touch/mouse)
  drm_display/   (cloned, untracked here)
  drm_screen/    (cloned, untracked here)
  drm_touch/     (cloned, untracked here)
  drm_composer/  (cloned, untracked here)
```

## License

MIT
