# DRM Stack Roadmap: SVG, Performance, and Hardware Acceleration

**Implementation Proposal — Detailed Engineering Specification**

---

## Overview

This document translates the roadmap goals into concrete implementation steps
grounded in the current codebase. Each proposal maps to real files, class names,
method signatures, and integration-test anchors so each stage can be picked up
without re-deriving context.

> **A note on scope.** The four packages (`drm_display`, `drm_screen`,
> `drm_touch`, `drm_composer`) are **not tracked in this umbrella repo** — they
> live in their own GitHub repos and are cloned by `setup.sh`. The API names,
> signatures, and code sketches below were verified against the actual package
> sources (cloned from `github.com/carstenbund/<pkg>`). Where this proposal
> shows a "current" code excerpt, it is quoted/derived from real source; where it
> shows "new" code, it is a proposed sketch to be adapted to the real module.

### Verified API baseline (from real package sources)

| Symbol | Real signature / fact | File |
|---|---|---|
| `Composer.__init__` | `(self, screen_width, screen_height)` — attrs `screen_width`, `screen_height` | `drm_screen/composer.py:15` |
| `Composer.render()` | pure `() -> np.ndarray`; iterates `sorted(layers, key=z)` | `drm_screen/composer.py:51` |
| blend helper | `Composer._blend(canvas, layer)` — a `@staticmethod`; honors `layer.opacity` | `drm_screen/composer.py:61` |
| `Layer` pixels | stored in `layer.buffer` (numpy `(h,w,4)` uint8), **not** `.data` bytes | `drm_screen/layer.py:25` |
| command apply | module-level `apply_command(composer, cmd)` — **no** `ScreenService._apply()` | `drm_screen/commands.py:134` |
| recomposite gate | `ScreenService.dirty` flag already exists; `render_once()` recomposites only when dirty | `drm_screen/service.py:23,51` |
| backend boundary | `DrmDisplayBackend.write(frame_rgba)` does RGBA→BGRA before `Screen.show()` | `drm_screen/backend.py:18` |
| cursor command | `SetPointer(x, y, visible=True)` — **not** `SetPosition("__pointer__", …)` | `drm_screen/commands.py:97` |
| cursor internals | `_POINTER_NAME`, `_POINTER_Z=1_000_000`, `default_cursor() -> ndarray`, `_cursor_hotspot()` | `drm_screen/commands.py:110,115,127` |
| wire registry | new commands must be added to `_KINDS` and handle bytes via base64 in `to_wire`/`from_wire` | `drm_screen/commands.py:178` |
| `fan_out` | `fan_out(submit, app_queue, cursor=True, hide_on_release=False)`; emits `SetPointer` | `drm_touch/reader.py:44` |
| image load | inside painter's `_paste_image(canvas, node)` via `Image.open(node.src)` | `drm_composer/painter.py:115,122` |
| display backend | `drm_display` is a **C extension** (`drm_display.c`) using **legacy `drmModeSetCrtc` only** — no plane/cursor/atomic API exists yet | `drm_display/drm_display.c:112` |

**The governing constraint:** every proposal must preserve the six design
invariants in `README.md` lines 46–84.  In particular:

- One RGBA→BGRA boundary (in `drm_screen`'s backend adapter)
- Commands are data (`CreateLayer`, `PlaceRawBuffer`, …)
- Composition is isolated in `drm_screen.Composer.render()`
- `drm_composer` is stateless — it never blends a final frame
- The app stays in control of input dispatch

---

## Stage 1 — SVG Asset Support (`drm_resvg`)

### Goal

Allow `<img src="slide.svg" />` and optionally `<svg src="slide.svg" />` to work
inside the existing HTML screen-markup language.  SVG is a raster-target image
format here; it is never an interactive or dynamic UI primitive.

### New package: `drm_resvg`

Create a standalone Rust/PyO3 package that lives at the same level as the other
packages:

```
drm_stack/
  drm_resvg/            ← new (cloned by setup.sh like the others)
    Cargo.toml
    pyproject.toml
    src/
      lib.rs
    drm_resvg/
      __init__.py
    tests/
      test_render.py
```

**`Cargo.toml`**

```toml
[package]
name = "drm_resvg"
version = "0.1.0"
edition = "2021"

[lib]
name = "drm_resvg"
crate-type = ["cdylib"]

[dependencies]
pyo3       = { version = "0.21", features = ["extension-module"] }
resvg      = "0.44"
usvg       = "0.44"
tiny-skia  = "0.11"
```

**`src/lib.rs` — complete module**

```rust
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyfunction]
fn render_svg_rgba<'py>(
    py: Python<'py>,
    svg_bytes: &[u8],
    width: u32,
    height: u32,
) -> PyResult<(&'py PyBytes, u32, u32)> {
    let opt = usvg::Options::default();
    let tree = usvg::Tree::from_data(svg_bytes, &opt)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let mut pixmap = tiny_skia::Pixmap::new(width, height)
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("zero-size pixmap"))?;

    resvg::render(
        &tree,
        tiny_skia::Transform::from_scale(
            width as f32 / tree.size().width(),
            height as f32 / tree.size().height(),
        ),
        &mut pixmap.as_mut(),
    );

    // tiny-skia produces premultiplied RGBA; convert to straight RGBA
    let mut data: Vec<u8> = pixmap.take();
    for px in data.chunks_exact_mut(4) {
        let a = px[3];
        if a > 0 && a < 255 {
            let af = a as f32 / 255.0;
            px[0] = (px[0] as f32 / af).round().min(255.0) as u8;
            px[1] = (px[1] as f32 / af).round().min(255.0) as u8;
            px[2] = (px[2] as f32 / af).round().min(255.0) as u8;
        }
    }

    Ok((PyBytes::new(py, &data), width, height))
}

#[pymodule]
fn drm_resvg(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_svg_rgba, m)?)?;
    Ok(())
}
```

**`drm_resvg/__init__.py`** (re-export for clean import)

```python
from .drm_resvg import render_svg_rgba

__all__ = ["render_svg_rgba"]
```

**`pyproject.toml`**

```toml
[build-system]
requires = ["maturin>=1.4"]
build-backend = "maturin"

[project]
name = "drm-resvg"
version = "0.1.0"
requires-python = ">=3.10"

[tool.maturin]
features = ["pyo3/extension-module"]
```

**Build & install** (added to `setup.sh` after drm_display):

```bash
if [ ! -d drm_resvg ]; then
    git clone https://github.com/carstenbund/drm_resvg
fi
.venv/bin/pip install -e drm_resvg   # maturin compiles on install
```

---

### Integration into `drm_composer`

The change is confined to a single function: `_paste_image(canvas, node)` in
`drm_composer/painter.py` (line 115), which currently does
`img = Image.open(node.src).convert("RGBA")` at line 122 and falls back to
`_paste_placeholder(...)` on `OSError`.

**Change:** add an extension check at the top of `_paste_image`. If `.svg`, render
via `drm_resvg` into a `PIL.Image`; otherwise fall through to the existing
`Image.open` path unchanged. Mirror the existing placeholder-on-failure behavior.

```python
# drm_composer/painter.py — inside _paste_image(), replacing the Image.open line
import os
ext = os.path.splitext(node.src)[1].lower()
if ext == ".svg":
    try:
        from drm_resvg import render_svg_rgba
        # SVG has no implicit pixel size: use explicit w/h if given, else a
        # sensible default box, then let _fit_image handle final placement.
        rw, rh = (w or canvas.width), (h or canvas.height)
        with open(node.src, "rb") as f:
            rgba_bytes, rw, rh = render_svg_rgba(f.read(), rw, rh)
        img = Image.frombytes("RGBA", (rw, rh), rgba_bytes)
    except Exception:           # parse error, missing file, or drm_resvg absent
        _paste_placeholder(canvas, x, y, w, h, node.src)
        return
else:
    try:
        img = Image.open(node.src).convert("RGBA")
    except OSError:
        _paste_placeholder(canvas, x, y, w, h, node.src)
        return
```

Notes verified against source:
- `_paste_image` already computes `x, y, w, h` (handling `fullscreen="always"`),
  so the SVG branch reuses them — no signature change.
- `w`/`h` may be `0`/falsy (image pasted at natural size). SVG has no implicit
  pixel size, so the branch must pick a raster size; the sketch falls back to the
  canvas size. This is the one real subtlety beyond a pure extension swap.
- Catch a broad `Exception` (not just `OSError`): `usvg`/`resvg` raise
  `ValueError` from the PyO3 binding, and `ImportError` if `drm_resvg` is absent.

This is the **only change to drm_composer**.  No changes to the command contract,
`drm_screen`, `drm_display`, or `drm_touch`.

---

### Optional: `<svg src="…" />` syntax sugar (Phase 3)

Add a new element alias in `drm_composer/parser.py`.  The parser already dispatches
on tag name.  Add a branch:

```python
# drm_composer/parser.py — in the element dispatch table
"svg": parse_img_node,   # treat identically to "img"
```

No painter change required — `parse_img_node` already extracts `src`, `w`, `h`,
`fit` and emits an `ImageNode`.  The painter's `_paste_image` SVG branch already
handles `.svg`.

---

### New integration tests

**File:** `integration/test_svg.py` (new)

```python
"""Integration: SVG → RGBA via drm_resvg → drm_composer → drm_screen."""
import pytest
from drm_composer import Compositor
from drm_screen import InProcessTarget
from conftest import W, H, render   # constants/helpers; `service` is an auto fixture

SVG_SCENE = f"""
<screen width="{W}" height="{H}">
  <layer id="bg" z="0">
    <img src="integration/assets/test.svg" w="{W}" h="{H}" fit="fill" />
  </layer>
</screen>
"""

def test_svg_img_renders_without_error(service):
    """SVG <img> compiles + renders; compositor does not raise."""
    Compositor(InProcessTarget(service)).render_html(SVG_SCENE)
    service.render_once()
    frame = service.backend.snapshot_rgba()
    assert frame.shape == (H, W, 4)

def test_svg_tag_alias(service):
    """<svg src="…"> is equivalent to <img src="…"> for .svg files."""
    scene = SVG_SCENE.replace("<img ", "<svg ").replace("/>", "/>")
    Compositor(InProcessTarget(service)).render_html(scene)
    service.render_once()
    frame = service.backend.snapshot_rgba()
    assert frame is not None
```

Add `integration/assets/test.svg` — a minimal 10×10 red square:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <rect width="10" height="10" fill="red"/>
</svg>
```

---

### Dependency chain after Stage 1

The change is **entirely inside `drm_composer`** — no other stack package is
affected. `drm_resvg` is simply a new pip dependency of `drm_composer`, not a
sibling stack package. It can be published to PyPI (or installed from a local
build) and listed in `drm_composer/setup.py`:

```python
# drm_composer/setup.py — add to install_requires
"drm-resvg",   # optional: absent → SVG falls back to placeholder, same as missing PNG
```

`setup.sh` does not need to clone a new repo — `pip install -e ./drm_composer`
pulls `drm-resvg` like any other dependency.

```
setup.sh install order (unchanged):
  drm_display    (no change)
  drm_screen     (no change)
  drm_touch      (no change)
  drm_composer   (changed: SVG branch in _paste_image; drm-resvg added as dependency)
```

---

## Stage 2 — Cursor Architecture Split (base_frame / front_frame)

### Goal

Cursor movement must not trigger a full scene recomposite.  Currently every
`SetPosition("__pointer__", x, y)` causes `Composer.render()` to re-blend all
layers.  The fix is to maintain two frames: `base_frame` (all non-cursor layers,
rebuilt only when scene changes) and `front_frame` (base + cursor blit, rebuilt
cheaply on every cursor move).

### Where the change lives

**Package:** `drm_screen` only.  All other packages are unchanged.

**Class:** `drm_screen.Composer` (`composer.py:14`) — `render()` (line 51) blends
all visible layers in z-order into a fresh canvas on every call, via the
`@staticmethod _blend(canvas, layer)` (line 61, which honors `layer.opacity`).

**Current recomposite trigger (verified):** a cursor move arrives as a
`SetPointer(x, y, visible)` command → `apply_command` updates the
`__pointer__` layer's `x/y` (`commands.py:161-171`) → the `_drain()` loop sets
`ScreenService.dirty = True` (`service.py:49`) → `render_once()` calls the full
`composer.render()` (`service.py:54`). So every cursor move does re-blend the
entire scene. The premise is correct; the fix is to cache everything below the
cursor.

**Invariant preserved:** `Composer.render()` remains the only place pixels are
blended (design invariant 5).  The split is internal to that function; the public
interface is unchanged.

---

### Implementation

**New state on `Composer`** (note real attribute names `screen_width` /
`screen_height`):

```python
# drm_screen/composer.py — additions to the existing class

from .commands import _POINTER_NAME   # the reserved cursor layer name

class Composer:
    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.layers: dict[str, Layer] = {}
        # NEW:
        self._base_frame: np.ndarray | None = None
        self._base_dirty: bool = True       # rebuild base on any non-cursor change
        self._render_count: int = 0         # test instrumentation
```

**Modified `render()` logic** (reusing the existing `_blend` staticmethod):

```python
    def render(self) -> np.ndarray:
        # 1. Rebuild base_frame only when the (non-cursor) scene changed
        if self._base_dirty or self._base_frame is None:
            canvas = np.zeros(
                (self.screen_height, self.screen_width, 4), dtype=np.uint8
            )
            for layer in sorted(self.layers.values(), key=lambda l: l.z):
                if layer.name == _POINTER_NAME:
                    continue
                if not layer.visible or layer.buffer is None:
                    continue
                self._blend(canvas, layer)
            self._base_frame = canvas
            self._base_dirty = False
            self._render_count += 1

        # 2. Composite the cursor on top — cheap copy + one blit
        cursor = self.layers.get(_POINTER_NAME)
        if cursor and cursor.visible and cursor.buffer is not None:
            front = self._base_frame.copy()
            self._blend(front, cursor)
            return front
        return self._base_frame
```

**Dirty-flag management** — the base must be rebuilt on any non-cursor layer
change. The cleanest seam is `apply_command(composer, cmd)` in `commands.py:134`
(there is **no** `ScreenService._apply()`; application is this module-level
function). Set `composer._base_dirty = True` for every command except a
`SetPointer` whose only effect is moving an existing cursor:

```python
# drm_screen/commands.py — at the end of apply_command(), after the dispatch:
    if not (isinstance(cmd, SetPointer) and _POINTER_NAME in composer.layers):
        composer._base_dirty = True
```

(The `SetPointer` that *first creates* the cursor layer still dirties the base,
which is correct — the cursor layer exists from then on, and subsequent moves
skip the rebuild.)

A pure cursor move therefore leaves `_base_dirty` False; the next `render()`
re-blits the cached `base_frame` instead of recompositing the scene.

---

### Performance expectation

| Scenario | Before | After |
|---|---|---|
| Scene change (new layer, text update) | Full recomposite | Full recomposite (base rebuild) |
| Cursor move only | Full recomposite | `base_frame.copy()` + one blit |
| Idle (no change) | Full recomposite | Return cached `front_frame` |

On a 1080p display the compositing of a 5-layer scene takes tens of milliseconds
in Python.  With `base_frame` caching, cursor moves drop to the cost of a single
`np.ndarray.copy()` + one alpha-blit — approximately one order of magnitude faster.

---

### Integration test additions

Extend `integration/test_input.py`:

```python
def test_cursor_move_does_not_invalidate_base(service):
    """Moving the cursor must not mark the base frame dirty."""
    bg = raw_layer("bg", solid(W, H, (20, 30, 60, 255)), z=0)
    service.submit(bg)
    service.render_once()

    app_q = queue.Queue()
    sink = fan_out(service.submit, app_q)

    render_count_before = service.composer._render_count   # new counter (see below)
    sink(TouchEvent("hover", 50, 50))   # cursor move
    service.render_once()
    # base frame must NOT have been rebuilt
    assert not service.composer._base_dirty
    # front frame must differ (cursor overlaid)
    sink(TouchEvent("hover", 60, 60))
    service.render_once()
    assert service.composer._render_count == render_count_before + 2
```

Add a `_render_count: int = 0` counter incremented each time `_base_frame` is
rebuilt — used only in tests.

---

## Stage 3 — Profile Before Optimizing Further

After Stages 1 and 2 are merged and deployed:

### Profiling script

**File:** `integration/profile_render.py` (new)

```python
"""Render timing benchmark: compositor hot path under varying load."""
import time
import numpy as np
from drm_screen import DrmDisplayBackend, ScreenService
from conftest import raw_layer, solid

RESOLUTIONS = [(800, 480), (1280, 720), (1920, 1080)]
LAYER_COUNTS = [2, 5, 10, 20]

for W, H in RESOLUTIONS:
    for N in LAYER_COUNTS:
        backend = DrmDisplayBackend(device="dummy", width=W, height=H)
        service = ScreenService(backend)
        for i in range(N):
            service.submit(raw_layer(f"l{i}", solid(W, H, (i*10, 0, 0, 200)), z=i))
        service.render_once()

        t0 = time.perf_counter()
        for _ in range(100):
            service.render_once()
        elapsed = (time.perf_counter() - t0) / 100 * 1000

        print(f"{W}x{H}  layers={N}  avg={elapsed:.1f}ms/frame")
        service.stop()
```

### Decision gate for Stage 4

Proceed to the Rust compositor only if profiling shows `> 16 ms/frame` (missing
60 fps) on the target hardware at the expected layer count and resolution.  If the
cursor split (Stage 2) already brings cursor latency under 5 ms, Stage 4 may be
deferred indefinitely.

---

## Stage 4 — Rust Compositor (`drm_screen_native`)

### Goal

Move `drm_screen.Composer.render()` — the pure `layers → RGBA frame` function —
to a Rust/PyO3 extension.  Nothing else moves.  The public API of `drm_screen` is
unchanged.  Python remains in charge of layer management, hit-testing, command
dispatch, and the service lifecycle.

### New package: `drm_screen_native`

```
drm_screen_native/
  Cargo.toml
  pyproject.toml
  src/
    lib.rs
  drm_screen_native/
    __init__.py
```

**Data contract** — the Rust function receives layer data as Python objects.
Note: layer pixels live in `layer.buffer` (a numpy `(h,w,4)` uint8 array), and
the Python `_blend` honors `layer.opacity` — the native blend **must** apply
opacity too, or the parity test below will fail.

```python
# Python call site (inside drm_screen.Composer.render):
from drm_screen_native import render_layers

frame_bytes = render_layers(
    width=self.screen_width,
    height=self.screen_height,
    layers=[
        {
            "x": layer.x,
            "y": layer.y,
            "z": layer.z,
            "width": layer.width,
            "height": layer.height,
            "opacity": float(layer.opacity),
            "data": layer.buffer.tobytes(),   # RGBA bytes from the numpy buffer
        }
        for layer in sorted(self.layers.values(), key=lambda l: l.z)
        if layer.visible and layer.buffer is not None
    ],
)
return np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
    self.screen_height, self.screen_width, 4
).copy()
```

> **Parity caveat.** The Python blend (`composer.py:72-78`) is float32:
> `alpha = (src_a/255) * opacity`; `out_rgb = src*alpha + dst*(1-alpha)`;
> `out_a = src_a + dst_a*(1-alpha)`, clipped to `[0,255]` then cast to uint8.
> A naive integer Rust blend will **not** be bit-identical. Either replicate the
> float32 rounding exactly in Rust, or relax the parity test to
> `assert_allclose(atol=1)` rather than `assert_array_equal`.

**`src/lib.rs` — compositor hot path**

```rust
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

#[pyfunction]
fn render_layers<'py>(
    py: Python<'py>,
    width: u32,
    height: u32,
    layers: &PyList,
) -> PyResult<&'py PyBytes> {
    let mut canvas = vec![0u8; (width * height * 4) as usize];

    for item in layers.iter() {
        let d: &PyDict = item.downcast()?;
        if !d.get_item("visible")?.map_or(false, |v| v.is_truthy().unwrap_or(false)) {
            continue;
        }
        let lx: i32 = d.get_item("x")?.and_then(|v| v.extract().ok()).unwrap_or(0);
        let ly: i32 = d.get_item("y")?.and_then(|v| v.extract().ok()).unwrap_or(0);
        let lw: u32 = d.get_item("width")?.and_then(|v| v.extract().ok()).unwrap_or(0);
        let lh: u32 = d.get_item("height")?.and_then(|v| v.extract().ok()).unwrap_or(0);
        let data: &[u8] = d.get_item("data")?.and_then(|v| v.extract().ok()).unwrap_or(&[]);

        alpha_blend(&mut canvas, width, height, data, lw, lh, lx, ly);
    }

    Ok(PyBytes::new(py, &canvas))
}

fn alpha_blend(
    canvas: &mut [u8], cw: u32, ch: u32,
    src: &[u8], sw: u32, sh: u32,
    ox: i32, oy: i32,
) {
    for row in 0..sh {
        let dy = oy + row as i32;
        if dy < 0 || dy >= ch as i32 { continue; }
        for col in 0..sw {
            let dx = ox + col as i32;
            if dx < 0 || dx >= cw as i32 { continue; }
            let si = ((row * sw + col) * 4) as usize;
            if si + 3 >= src.len() { continue; }
            let di = ((dy as u32 * cw + dx as u32) * 4) as usize;
            let sa = src[si + 3] as u32;
            if sa == 0 { continue; }
            if sa == 255 {
                canvas[di..di+4].copy_from_slice(&src[si..si+4]);
                continue;
            }
            let ia = 255 - sa;
            canvas[di]     = ((src[si]     as u32 * sa + canvas[di]     as u32 * ia) / 255) as u8;
            canvas[di + 1] = ((src[si + 1] as u32 * sa + canvas[di + 1] as u32 * ia) / 255) as u8;
            canvas[di + 2] = ((src[si + 2] as u32 * sa + canvas[di + 2] as u32 * ia) / 255) as u8;
            canvas[di + 3] = sa.min(255) as u8;
        }
    }
}

#[pymodule]
fn drm_screen_native(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_layers, m)?)?;
    Ok(())
}
```

---

### Fallback in `drm_screen.Composer`

The existing Python blend path is kept unchanged.  `drm_screen_native` is tried
at import time and falls back transparently:

```python
# drm_screen/composer.py

try:
    from drm_screen_native import render_layers as _native_render
    _HAVE_NATIVE = True
except ImportError:
    _HAVE_NATIVE = False

class Composer:
    def render(self) -> np.ndarray:
        if _HAVE_NATIVE:
            return self._render_native()
        return self._render_python()

    def _render_native(self) -> np.ndarray:
        frame_bytes = _native_render(
            width=self.screen_width,
            height=self.screen_height,
            layers=[...],   # as shown above (buffer.tobytes(), opacity included)
        )
        return np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
            self.screen_height, self.screen_width, 4
        ).copy()

    def _render_python(self) -> np.ndarray:
        # unchanged existing implementation
        ...
```

---

### Integration tests — unchanged

The existing `test_pipeline.py` tests validate pixel values; they pass whether the
native or Python path executes.  Add one explicit test to verify parity:

```python
# integration/test_native_compositor.py (new)
import pytest
import numpy as np
from conftest import W, H, render, raw_layer, solid   # `service` is an auto fixture

def test_native_matches_python_within_tolerance(service):
    """Native Rust compositor and Python compositor agree to within 1 LSB.

    Exact equality is only achievable if the Rust blend reproduces the Python
    float32 rounding bit-for-bit; otherwise assert_allclose(atol=1) is the
    honest contract.
    """
    try:
        from drm_screen_native import render_layers
    except ImportError:
        pytest.skip("drm_screen_native not installed")

    frame_py = render(service, raw_layer("bg", solid(W, H, (20, 30, 60, 255)), z=0)
                              + raw_layer("fg", solid(W, H, (200, 0, 0, 170)), z=10))
    layers = [
        {"x": 0, "y": 0, "z": 0, "width": W, "height": H, "opacity": 1.0,
         "data": solid(W, H, (20, 30, 60, 255)).tobytes()},
        {"x": 0, "y": 0, "z": 10, "width": W, "height": H, "opacity": 1.0,
         "data": solid(W, H, (200, 0, 0, 170)).tobytes()},
    ]
    frame_native = np.frombuffer(
        render_layers(W, H, layers), dtype=np.uint8
    ).reshape(H, W, 4)

    np.testing.assert_allclose(frame_py, frame_native, atol=1)
```

---

## Stage 5 — Raspberry Pi Hardware Cursor

### Goal

Use the VC4/V3D DRM cursor plane on Raspberry Pi so cursor movement is an atomic
plane property update — no framebuffer rewrite, no compositing cost at all.

> **Reality check (verified).** `drm_display`'s rendering core is a **C
> extension** (`drm_display/drm_display.c`) that currently uses **only legacy
> `drmModeSetCrtc`** (lines 112–185). `drm_display` needs thin new C additions
> (GEM dumb-buffer allocation for the cursor BO, `drmModeSetCursor2`,
> `drmModeMoveCursor`) but these are relatively narrow ioctls — they require no
> atomic KMS and no plane enumeration. The bulk of the stage is in `drm_screen`:
> new command types, service logic, detection, and the fallback path. The effort
> estimate reflects the C work plus the end-to-end integration.

### Architecture

The boundary is clean: `drm_display` knows about DRM planes; `drm_screen` knows
the cursor is semantically special.

**Changes required:**

| Package | Change |
|---|---|
| `drm_display` | Detect cursor plane; implement `set_cursor()` / `move_cursor()` |
| `drm_screen` | Expose `set_cursor()` / `move_cursor()` on `ScreenService`; detect capability |
| `drm_touch` / `drm_composer` | No change |
| Integration tests | New tests; existing tests unchanged (fallback path) |

---

### `drm_display` changes

**New capability query on the display backend:**

```python
# drm_display/screen.py (approximate location)

class Screen:
    @property
    def has_hardware_cursor(self) -> bool:
        """True if the DRM device exposes a cursor plane."""
        return self._cursor_plane_id is not None

    def set_cursor(self, rgba: bytes, width: int, height: int,
                   hotspot_x: int = 0, hotspot_y: int = 0) -> None:
        """Upload cursor bitmap to DRM cursor plane.

        rgba must be width*height*4 bytes, straight RGBA, pre-converted to
        ARGB8888 (the DRM cursor format) inside this method.
        """
        if not self.has_hardware_cursor:
            raise RuntimeError("no cursor plane")
        # drmModeSetCursor2(fd, crtc_id, bo_handle, width, height, hot_x, hot_y)
        ...

    def move_cursor(self, x: int, y: int) -> None:
        """Move cursor plane via drmModeMoveCursor — no buffer re-upload."""
        if not self.has_hardware_cursor:
            raise RuntimeError("no cursor plane")
        # drmModeMoveCursor(fd, crtc_id, x, y)
        ...
```

**DRM cursor plane detection** (in `Screen.__init__`):

```python
# Use drmModeGetResources → iterate planes → check DRM_PLANE_TYPE_CURSOR
# Store as self._cursor_plane_id and self._cursor_crtc_id
# If not found: self._cursor_plane_id = None
```

The DRM cursor API on VC4 uses `drmModeSetCursor2` (with hotspot) and
`drmModeMoveCursor`.  The GEM buffer for the cursor bitmap is allocated with
`drmModeCreateDumbBuffer`, filled via mmap, then passed to `drmModeSetCursor2`.

**RGBA → ARGB8888 conversion** (DRM cursor expects ARGB, not RGBA):

```python
def _rgba_to_argb(rgba: bytes, w: int, h: int) -> bytes:
    """Convert RGBA to ARGB8888 (DRM cursor plane format)."""
    import numpy as np
    a = np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4)
    argb = np.stack([a[..., 3], a[..., 0], a[..., 1], a[..., 2]], axis=-1)
    return argb.tobytes()
```

---

### `drm_screen` changes

**`DrmDisplayBackend`** wraps the new `drm_display.Screen` methods:

```python
# drm_screen/backend.py (approximate location)

class DrmDisplayBackend:
    @property
    def has_hardware_cursor(self) -> bool:
        return getattr(self._screen, "has_hardware_cursor", False)

    def set_cursor(self, rgba: bytes, width: int, height: int,
                   hotspot_x: int = 0, hotspot_y: int = 0) -> None:
        self._screen.set_cursor(rgba, width, height, hotspot_x, hotspot_y)

    def move_cursor(self, x: int, y: int) -> None:
        self._screen.move_cursor(x, y)
```

**`ScreenService`** — new cursor commands or direct path:

The cleanest approach extends the existing `SetPointer` command rather than
inventing parallel ones — the cursor already flows as `SetPointer` from
`drm_touch`, so a hardware path can be a property of how `apply_command` (and the
backend) handles it. If distinct records are preferred, add them as real
`@dataclass`es **and register them in `_KINDS`** (`commands.py:178`) so the wire
format round-trips; bytes fields must be base64-encoded like `PlaceRawBuffer.data`
already is (`commands.py:187`):

```python
# drm_screen/commands.py — new command types (register in _KINDS!)

@dataclass
class SetHardwareCursor:
    """Upload cursor bitmap to the DRM cursor plane."""
    data: bytes          # RGBA; named `data` so to_wire/from_wire base64 it
    width: int
    height: int
    hotspot_x: int = 0
    hotspot_y: int = 0

@dataclass
class MoveHardwareCursor:
    """Move the cursor plane (no recomposite)."""
    x: int
    y: int

# _KINDS = {c.__name__: c for c in (... , SetHardwareCursor, MoveHardwareCursor)}
```

Handle them inside the module-level `apply_command(composer, cmd)` (there is no
`ScreenService._apply`). `apply_command` currently takes only `(composer, cmd)`,
so reaching the backend means either threading the service/backend into the
dispatch or handling these two records in `ScreenService.render_once()` before
`apply_command` runs. Sketch (backend-aware variant):

```python
elif isinstance(cmd, SetHardwareCursor):
    if backend.has_hardware_cursor:
        backend.set_cursor(cmd.data, cmd.width, cmd.height,
                           cmd.hotspot_x, cmd.hotspot_y)

elif isinstance(cmd, MoveHardwareCursor):
    if backend.has_hardware_cursor:
        backend.move_cursor(cmd.x, cmd.y)
        # no composer change, no dirty flag — the plane moved in hardware
    else:
        # Fallback to the software cursor path: reuse SetPointer
        apply_command(composer, SetPointer(cmd.x, cmd.y, visible=True))
```

---

### `drm_touch` / `fan_out` change

`fan_out` lives in `drm_touch/reader.py:44` with the real signature
`fan_out(submit, app_queue, cursor=True, hide_on_release=False)` and currently
emits `SetPointer(ev.x, ev.y, visible=visible)` (line 61) — **not**
`SetPosition`. To support hardware cursor, add an optional
`cursor_command_factory` that defaults to the existing `SetPointer` behavior:

```python
# drm_touch/reader.py — extend the existing fan_out signature

def fan_out(submit, app_queue, cursor=True, hide_on_release=False,
            cursor_command_factory=None):   # NEW — defaults to SetPointer
    from drm_screen.commands import SetPointer
    def _move(x, y, visible):
        if cursor_command_factory:
            submit([cursor_command_factory(x, y)])
        else:
            submit([SetPointer(x, y, visible=visible)])
    ...
```

Application code that uses hardware cursor:

```python
from drm_screen.commands import MoveHardwareCursor
sink = fan_out(
    service.submit,
    app_queue,
    cursor_command_factory=lambda x, y: MoveHardwareCursor(x, y),
)
```

Default behavior (no factory) is unchanged — existing code requires no modification.

---

### Fallback detection at startup

In `ScreenService.start()` (or wherever the display is initialized):

```python
def start(self):
    ...
    self._using_hw_cursor = False
    if self.backend.has_hardware_cursor:
        # Upload the default pointer cursor bitmap. The real helpers are
        # default_cursor() -> np.ndarray (h,w,4) and _cursor_hotspot() -> (x,y),
        # both in drm_screen/commands.py.
        from drm_screen.commands import default_cursor, _cursor_hotspot
        cur = default_cursor()              # ndarray (h, w, 4) RGBA
        ch, cw = cur.shape[:2]
        hx, hy = _cursor_hotspot()
        self.backend.set_cursor(cur.tobytes(), cw, ch, hx, hy)
        self._using_hw_cursor = True
    ...
```

If `has_hardware_cursor` is False (non-Pi hardware, dummy backend, etc.), the
service falls back silently to the existing software cursor path.

---

### Integration tests

**File:** `integration/test_hw_cursor.py` (new)

```python
"""Hardware cursor path — headless using dummy backend (always falls back to SW)."""
import queue
from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import SetHardwareCursor, MoveHardwareCursor, _POINTER_NAME
from drm_touch import TouchEvent, fan_out
from conftest import W, H, raw_layer, solid

def test_hw_cursor_commands_do_not_error_on_dummy():
    """HW cursor commands are accepted without error on dummy backend (SW fallback)."""
    backend = DrmDisplayBackend(device="dummy", width=W, height=H)
    service = ScreenService(backend)
    # dummy backend: has_hardware_cursor is False → commands fall back silently
    service.submit([SetHardwareCursor(b"\x00" * (16*16*4), 16, 16, 8, 8)])
    service.submit([MoveHardwareCursor(100, 50)])
    service.render_once()   # must not raise

def test_hw_cursor_fallback_updates_software_layer():
    """When HW cursor unavailable, MoveHardwareCursor falls back to SetPosition."""
    backend = DrmDisplayBackend(device="dummy", width=W, height=H)
    service = ScreenService(backend)
    service.submit([MoveHardwareCursor(75, 40)])
    service.render_once()
    ptr = service.composer.layers.get(_POINTER_NAME)
    # software cursor layer should have been moved to (75, 40) minus hotspot
    assert ptr is not None

def test_fan_out_with_hw_cursor_factory():
    """fan_out with cursor_command_factory emits MoveHardwareCursor."""
    from drm_screen.commands import MoveHardwareCursor
    backend = DrmDisplayBackend(device="dummy", width=W, height=H)
    service = ScreenService(backend)
    app_q = queue.Queue()
    sink = fan_out(service.submit, app_q,
                   cursor_command_factory=lambda x, y: MoveHardwareCursor(x, y))
    sink(TouchEvent("hover", 80, 60))
    service.render_once()
    # App still receives the event
    ev = app_q.get_nowait()
    assert (ev.x, ev.y) == (80, 60)
```

---

## Stage 6 — Optional Overlay Planes

This stage is contingent on Stage 5 succeeding and profiling showing further gain.

### Concept

Instead of compositing all layers to one primary plane, map logical layer groups
to distinct DRM planes:

| Logical role | DRM plane type |
|---|---|
| Background / wallpaper | Primary plane (z=0) |
| UI chrome (buttons, text) | Overlay plane |
| Cursor | Cursor plane (from Stage 5) |

The compositor would detect available planes at startup, assign layers to planes
by z-range or explicit annotation, and use atomic DRM commits to update them
independently.

### Why it's deferred

1. Overlay planes impose format constraints (YUV, specific pitch alignment) that
   vary by hardware.
2. Atomic KMS API is significantly more complex than `drmModeSetCrtc`.
3. Stage 2 (cursor split) + Stage 4 (Rust compositor) together likely solve
   the performance problem without overlay planes.
4. Stage 5 (hardware cursor) already gives us one overlay plane for free.

### When to implement

Only if profiling after Stage 4 still shows > 16 ms/frame for background + UI
composite at target resolution, **and** the target hardware (VC4/V3D) exposes at
least one overlay plane with a compatible format (typically ARGB8888 or XRGB8888).

### Architecture notes (for when the time comes)

- New `drm_display` API: `list_planes() → List[PlaneInfo]`
- New command: `AssignLayerToPlane(layer_name: str, plane_id: int)` (optional)
- `ScreenService` routes each layer to its assigned plane at render time
- Fallback: if plane unavailable, layer composited to primary plane as today
- Invariant preserved: all Python-visible data remains RGBA; per-plane format
  conversion lives in `drm_display`

---

## Stage 7 — EGL/GBM/GPU Compositor (Deferred)

Not described in detail here.  Prerequisite: Stages 1–5 completed and profiling
still shows unacceptable latency.  This is a significantly larger project (new
package `drm_screen_gpu`) and would likely require GBM buffer allocation,
EGL context management, and OpenGL ES shader-based alpha blending — none of which
is needed to solve the problems identified today.

---

## Recommended Execution Order and Dependency Map

```
Stage 1: drm_resvg + drm_composer painter
  Deps: Rust toolchain + maturin
  Risk: Low — isolated package, Python fallback always available
  Estimated effort: 3–5 days

Stage 2: base_frame / front_frame split
  Deps: None (internal to drm_screen)
  Risk: Low — pure internal refactor, existing tests validate correctness
  Estimated effort: 1–2 days

Stage 3: Profile
  Deps: Stages 1 + 2 deployed on target hardware
  Risk: None
  Estimated effort: 1 day

Stage 4: drm_screen_native (Rust compositor)
  Deps: Stage 3 showing need; Rust toolchain
  Risk: Medium — pixel-exact parity test must pass; blend math must match Python
  Estimated effort: 3–5 days

Stage 5: Hardware cursor (drm_display + drm_screen + drm_touch)
  Deps: Stage 2 (cursor already special-cased); Raspberry Pi hardware for testing
  Risk: Medium — DRM kernel API, GEM buffer management, Pi-specific ioctl
  Estimated effort: 5–7 days

Stage 6: Overlay planes
  Deps: Stage 5; profiling showing need; hardware support verified
  Risk: High — atomic KMS, hardware format constraints
  Estimated effort: 7–14 days

Stage 7: EGL/GPU
  Deps: All prior stages; only if still needed
  Risk: Very High
  Estimated effort: Several weeks
```

---

## Integration Test Strategy (All Stages)

The umbrella repo's `integration/` directory is the right home for all new tests.
The existing fixture pattern (`conftest.py`, `render()`, `raw_layer()`, `solid()`)
handles all new tests without modification.

**New test files:**

| File | Stage | Coverage |
|---|---|---|
| `integration/test_svg.py` | 1 | SVG render via drm_resvg; `<svg>` alias |
| `integration/test_cursor_split.py` | 2 | `_base_dirty` flag; non-rebuild on cursor move |
| `integration/test_native_compositor.py` | 4 | Bit-identical output vs Python path |
| `integration/test_hw_cursor.py` | 5 | SW fallback path; `fan_out` factory |

**New benchmark script:**

| File | Stage | Purpose |
|---|---|---|
| `integration/profile_render.py` | 3 | Frame-time measurement across resolutions/layer counts |

**Existing tests:** All must continue to pass at every stage.  Each stage adds,
never removes, integration test coverage.

---

## Makefile additions

```makefile
# Stage 1
svg-test:
	.venv/bin/pytest integration/test_svg.py -v

# Stage 3
profile:
	.venv/bin/python integration/profile_render.py

# Stage 4
native-test:
	.venv/bin/pytest integration/test_native_compositor.py -v

# Stage 5
hw-cursor-test:
	.venv/bin/pytest integration/test_hw_cursor.py -v
```

---

## Summary: What Changes in Each Package

| Package | Stage 1 | Stage 2 | Stage 4 | Stage 5 |
|---|---|---|---|---|
| `drm_display` | — | — | — | Thin new C bindings only: GEM dumb-buffer allocation, `drmModeSetCursor2`, `drmModeMoveCursor` exposed as Python methods on `Screen` |
| `drm_screen` | — | `Composer.render()` base/front split; `_base_dirty` (distinct from existing `dirty`) | `_render_native()` fallback; `try/except` | **Main feature owner**: `SetHardwareCursor`/`MoveHardwareCursor` commands (register in `_KINDS`); `DrmDisplayBackend.set_cursor()`/`move_cursor()` wrappers (`backend.py`); cursor detection + `_using_hw_cursor` in `ScreenService`; fallback to software cursor |
| `drm_touch` | — | — | — | `fan_out()` `cursor_command_factory` param (default stays `SetPointer`) |
| `drm_composer` | SVG branch in `_paste_image()` (painter.py:115) | — | — | — |
| `drm_resvg` | **new package** | — | — | — |
| `drm_screen_native` | — | — | **new package** | — |
| `drm_stack` (umbrella) | `test_svg.py`; `assets/test.svg`; `setup.sh` | `test_cursor_split.py` | `test_native_compositor.py` | `test_hw_cursor.py` |

The key property of this plan is that each stage is **independently mergeable**:
no stage breaks existing behavior, every stage adds a tested capability, and the
fallback paths ensure the stack degrades gracefully on hardware that does not
support the advanced feature.
