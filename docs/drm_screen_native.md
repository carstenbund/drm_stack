# drm_screen_native — New Package Specification

`drm_screen_native` is a new, standalone Rust/PyO3 package. It is an **optional
pip dependency of `drm_screen`** — if absent, `drm_screen` falls back silently to
its Python compositor. The public API is a single function.

---

## Package layout

```
drm_screen_native/
  Cargo.toml
  pyproject.toml
  src/
    lib.rs
  drm_screen_native/
    __init__.py
  tests/
    test_blend.py
```

---

## `Cargo.toml`

```toml
[package]
name = "drm_screen_native"
version = "0.1.0"
edition = "2021"

[lib]
name = "drm_screen_native"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.21", features = ["extension-module"] }
```

---

## `pyproject.toml`

```toml
[build-system]
requires = ["maturin>=1.4"]
build-backend = "maturin"

[project]
name = "drm-screen-native"
version = "0.1.0"
requires-python = ">=3.10"

[tool.maturin]
features = ["pyo3/extension-module"]
```

---

## Public API

```python
frame_bytes: bytes = render_layers(
    width:  int,
    height: int,
    layers: list[dict],   # see layer dict schema below
) -> bytes
```

Returns `width × height × 4` bytes, straight RGBA, row-major. Layers are
composited in ascending `z` order. The function is a pure transform; it holds
no state.

**Layer dict schema:**

```python
{
    "x":       int,    # layer origin (may be negative)
    "y":       int,
    "z":       int,    # used by caller to sort before passing; not used inside
    "width":   int,    # layer bitmap width
    "height":  int,    # layer bitmap height
    "opacity": float,  # 0.0–1.0; multiplied into the alpha channel
    "data":    bytes,  # width*height*4 straight RGBA
}
```

The caller (`drm_screen.Composer._render_native`) is responsible for:
- Filtering out invisible layers and the cursor layer before calling.
- Sorting by `z` and passing the sorted list.

---

## `src/lib.rs`

```rust
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

#[pyfunction]
fn render_layers<'py>(
    py:     Python<'py>,
    width:  u32,
    height: u32,
    layers: &PyList,
) -> PyResult<&'py PyBytes> {
    let mut canvas = vec![0u8; (width * height * 4) as usize];

    for item in layers.iter() {
        let d: &PyDict = item.downcast()?;

        let lx: i32  = extract(d, "x")?;
        let ly: i32  = extract(d, "y")?;
        let lw: u32  = extract(d, "width")?;
        let lh: u32  = extract(d, "height")?;
        let op: f32  = extract(d, "opacity")?;
        let src: &[u8] = d.get_item("data")
            .and_then(|v| v.and_then(|v| v.extract().ok()))
            .unwrap_or(&[]);

        alpha_blend(&mut canvas, width, height, src, lw, lh, lx, ly, op);
    }

    Ok(PyBytes::new(py, &canvas))
}

fn extract<'py, T: pyo3::FromPyObject<'py>>(
    d: &'py PyDict,
    key: &str,
) -> PyResult<T> {
    d.get_item(key)?
        .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err(key.to_string()))?
        .extract()
}

/// Alpha-composite src over canvas, honoring per-layer opacity.
///
/// Mirrors the Python blend in drm_screen/composer.py lines 72–78:
///   alpha = (src_a / 255.0) * opacity
///   out_rgb = src_rgb * alpha + dst_rgb * (1 - alpha)
///   out_a   = src_a + dst_a * (1 - alpha)
/// Uses f32 arithmetic to stay within ±1 LSB of the Python result.
fn alpha_blend(
    canvas: &mut [u8],
    cw: u32, ch: u32,
    src: &[u8],
    sw: u32, sh: u32,
    ox: i32, oy: i32,
    opacity: f32,
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

            let src_r = src[si]     as f32;
            let src_g = src[si + 1] as f32;
            let src_b = src[si + 2] as f32;
            let src_a = src[si + 3] as f32;

            let alpha   = (src_a / 255.0) * opacity;
            let inv     = 1.0 - alpha;

            let dst_r = canvas[di]     as f32;
            let dst_g = canvas[di + 1] as f32;
            let dst_b = canvas[di + 2] as f32;
            let dst_a = canvas[di + 3] as f32;

            canvas[di]     = (src_r * alpha + dst_r * inv).round().clamp(0.0, 255.0) as u8;
            canvas[di + 1] = (src_g * alpha + dst_g * inv).round().clamp(0.0, 255.0) as u8;
            canvas[di + 2] = (src_b * alpha + dst_b * inv).round().clamp(0.0, 255.0) as u8;
            canvas[di + 3] = (src_a + dst_a * inv).round().clamp(0.0, 255.0) as u8;
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

## `drm_screen_native/__init__.py`

```python
from .drm_screen_native import render_layers

__all__ = ["render_layers"]
```

---

## Parity with Python compositor

The `alpha_blend` function above mirrors `Composer._blend` (Python,
`composer.py:61–78`) using f32 arithmetic throughout. Results should agree with
the Python compositor within ±1 LSB per channel (rounding may differ at the 0.5
boundary). The integration parity test therefore uses `atol=1`, not exact
equality.

---

## Unit tests (`tests/test_blend.py`)

```python
import numpy as np
from drm_screen_native import render_layers

def solid(w, h, rgba):
    return np.full((h, w, 4), rgba, dtype=np.uint8).tobytes()

def test_opaque_layer_covers_background():
    layers = [
        {"x": 0, "y": 0, "z": 0, "width": 4, "height": 4,
         "opacity": 1.0, "data": solid(4, 4, (20, 30, 60, 255))},
        {"x": 0, "y": 0, "z": 1, "width": 4, "height": 4,
         "opacity": 1.0, "data": solid(4, 4, (255, 0, 0, 255))},
    ]
    frame = np.frombuffer(render_layers(4, 4, layers), dtype=np.uint8).reshape(4, 4, 4)
    assert tuple(frame[2, 2][:3]) == (255, 0, 0)

def test_alpha_blend_math():
    # out = src*alpha + dst*(1-alpha); opacity=1, src_a=128 → alpha≈0.502
    layers = [
        {"x": 0, "y": 0, "z": 0, "width": 1, "height": 1,
         "opacity": 1.0, "data": bytes([0, 0, 60, 255])},
        {"x": 0, "y": 0, "z": 1, "width": 1, "height": 1,
         "opacity": 1.0, "data": bytes([0, 0, 0, 128])},
    ]
    frame = np.frombuffer(render_layers(1, 1, layers), dtype=np.uint8)
    # blue channel: 0*0.502 + 60*0.498 ≈ 29.9 → 30
    assert abs(int(frame[2]) - 30) <= 1

def test_opacity_multiplier():
    # A fully-opaque red layer at opacity=0.5 behaves like alpha=128
    layers = [
        {"x": 0, "y": 0, "z": 0, "width": 1, "height": 1,
         "opacity": 1.0, "data": bytes([0, 0, 60, 255])},
        {"x": 0, "y": 0, "z": 1, "width": 1, "height": 1,
         "opacity": 0.5, "data": bytes([0, 0, 0, 255])},
    ]
    frame = np.frombuffer(render_layers(1, 1, layers), dtype=np.uint8)
    assert abs(int(frame[2]) - 30) <= 1

def test_layer_clipping():
    # A 2x2 layer placed at (-1, -1) should only write the one visible pixel
    layers = [
        {"x": 0, "y": 0, "z": 0, "width": 2, "height": 2,
         "opacity": 1.0, "data": solid(2, 2, (20, 30, 60, 255))},
        {"x": -1, "y": -1, "z": 1, "width": 2, "height": 2,
         "opacity": 1.0, "data": solid(2, 2, (255, 0, 0, 255))},
    ]
    frame = np.frombuffer(render_layers(2, 2, layers), dtype=np.uint8).reshape(2, 2, 4)
    assert tuple(frame[0, 0][:3]) == (255, 0, 0)   # covered by red
    assert tuple(frame[1, 1][:3]) == (20, 30, 60)  # outside the shifted layer
```
