# drm_stack

A lightweight screen manager that receives declarative scenes and renders them
directly to a Linux display via DRM/KMS — no X11, Wayland, or browser engine.

This is the **umbrella repo**: stack-level docs, a dev bootstrap, and an
integration demo.  The three packages live in their own repos (cloned in by
`setup.sh`); this repo never tracks their contents.

## The stack

```
Application
   ↓  declarative screen-HTML
drm_composer   scene markup → commands + RGBA bitmaps      (stateless compiler)
   ↓  command batch over submit() / socket
drm_screen     layers → composited RGBA frame             (stateful service)
   ↓  frame
drm_display    frame → DRM/KMS pixels                      (hardware backend)
```

| Package | Role | Owns | Never does | Repo |
|---|---|---|---|---|
| [`drm_composer`](https://github.com/carstenbund/drm_composer) | scene-to-command compiler | parse HTML, layout, rasterize to RGBA, emit commands | hold screen state, blend, touch DRM | drm_composer |
| [`drm_screen`](https://github.com/carstenbund/drm_screen) | screen manager / service | persistent layers, **compositing**, command API, render loop | parse HTML, touch DRM | drm_screen |
| [`drm_display`](https://github.com/carstenbund/drm_display) | low-level output | DRM/KMS, framebuffer, headless backends | anything above pixels | drm_display |

`drm_display` is also an independently published library (PyPI: `drm-display`);
the other two depend downward only.

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

## Quick start

```bash
git clone https://github.com/carstenbund/drm_stack
cd drm_stack
./setup.sh                          # clones the 3 packages + editable-installs into .venv
source .venv/bin/activate
python integration/stack_demo.py    # headless end-to-end; writes integration/stack_frame.png
```

`setup.sh` is idempotent: existing package clones are left as-is, only missing
ones are cloned.

## Testing

The umbrella is where the three packages are tested *together* — the boundaries
no single repo can cover (HTML→command contract, command→composite, the
RGBA→BGRA hardware boundary).  All headless, no display required.

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

## Layout

```
drm_stack/
  README.md                 # this file — the canonical stack overview
  setup.sh                  # clone + editable-install bootstrap
  Makefile                  # setup / test / demo / clean targets
  pytest.ini                # integration test config
  integration/
    conftest.py             # headless fixtures (synchronous render)
    test_pipeline.py        # cross-package integration tests
    stack_demo.py           # end-to-end demo across all three packages
  drm_display/   (cloned, untracked here)
  drm_screen/    (cloned, untracked here)
  drm_composer/  (cloned, untracked here)
```

## License

MIT
