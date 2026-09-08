# drm_screen_lvgl — LVGL renderer plugin (Stage 6)

**Status:** implemented, unreleased. Repo:
[`drm_screen_lvgl`](https://github.com/carstenbund/drm_screen_lvgl).

Stage 4 moves `Composer.render()` — the `layers → RGBA frame` blend — into Rust.
This stage asks the question one step further out: **does a frame need to be
formed at all?**

Where LVGL is available, no. It holds the layers itself, composites its own
dirty rectangles, and presents through DRM/KMS directly. Nothing rasterizes a
whole screen because a badge moved, nothing converts RGBA→BGRA, and a layer can
carry a **description** instead of pixels.

```
rgba   commands → numpy layers → whole-frame blend → RGBA→BGRA
                → drm_display → DRM/KMS
lvgl   commands → LVGL objects → LVGL's dirty-area composite → DRM/KMS
```

## Why this is not a fork of drm_screen

`drm_screen`'s value was never the numpy blend. It is the vocabulary: named
persistent layers, z-order, visibility, opacity, hit-testing, `submit()` a batch
of records that survive a socket hop. That vocabulary is what `drm_composer`
targets and what applications are written against, and it does not change here.

What changed in `drm_screen` is a seam — `drm_screen/renderers.py` — and one new
command. Everything else in this stage is a separate, optional package.

## Measured

On this stack's reference host (software rasterization, one draw unit, no GPU):

| | 1280×800 | 1920×1080 | 2560×1600 | 4096×2160 |
|---|---|---|---|---|
| ms per frame | 0.7 | 1.5 | 2.8 | 6.2 |
| fps | 1323 | 625 | 331 | 135 |

Those are for a full-screen animated **scene layer** — a 1.4 KB document, no
bitmap in the path at any point. Cost tracks pixel count almost exactly, which
is what software rasterization of vector paths should do: the path work is
constant, the fill is linear in area.

Through the layer model with three layers (scene + moving bitmap + pointer) at
1920×1080 the same content costs 3.1 ms. The difference is the scene layer's
own canvas being composited onto the root; a full-screen scene layer with
nothing under it can draw into the display's layer directly, which has not been
done because 320 fps has not needed it.

## Changes in `drm_screen` (done)

| | |
|---|---|
| `renderers.py` | `Renderer` protocol, capability names, `RgbaRenderer` (the existing path, unchanged), discovery through the `drm_screen.renderers` entry-point group |
| `service.py` | `ScreenService(renderer=…, renderer_options=…, clock=…)`; `render_once(scene_time_ms)`; `hit_test` delegates to the renderer |
| `commands.py` | `PlaceScene`, `SetOpacity`, `UnsupportedCommand`; wire format carries a scene as bytes or as text |
| `tests/` | the seam, with a stub backend and a fake plugin — no hardware, no plugin needed |

**Back-compatibility rule:** `renderer="auto"` gives a caller that passed a
backend exactly the path it has always had. Opting in is by name, by omitting
the backend, or by `DRM_SCREEN_RENDERER=lvgl` — which moves an existing
deployment with no code change at all.

A plugin may expose `available()`; one that answers False (no native library) is
installed but not offered, so `auto` never picks something it cannot construct.

## Changes in `drm_composer` (done)

`<path>` and `<animate>` — a layer of paths compiles to `PlaceScene` carrying a
`drm_scene_ir` document rather than to a rasterized bitmap. See that repo's
`SYNTAX.md`. Animating `progress` is what makes a line draw itself; that is the
one thing a bitmap layer structurally cannot express.

## The native library

`libdrm_screen_lvgl` is LVGL, ThorVG and the scene evaluator in portable C — the
same sources that run on an ESP32-S3, which is the point: a panel and a Pi draw
the same picture from the same bytes.

It lives in [`mementum-lcd`](https://github.com/carstenbund/mementum-lcd) while
the scene format is being settled, and is found through `DRM_SCREEN_LVGL_LIB`
or `MEMENTUM_SRC`. It moves into this repo's build when `drm_scene_ir` is
versioned.

## Not done

- **Scene layer straight to the display layer** — skip the intermediate canvas
  when a scene layer covers the screen (3.1 ms → ~1.5 ms at 1080p).
- **`<symbol src="…svg">`** — an SVG converter, not a parser branch: outlines
  must become ordered stroke data before they can be *drawn* rather than filled.
- **Transforms and deformations in markup** — the renderer has them; the
  authoring language does not yet.
- **`drm_scene_ir` as its own versioned repo**, with the conformance suite both
  players pass.
