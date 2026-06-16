# drm_resvg — New Package Specification

`drm_resvg` is a new, standalone Rust/PyO3 package. It is a **pip dependency of
`drm_composer`**, not a cloned stack package — `setup.sh` does not need to change.

Its sole purpose: rasterize an SVG document to a straight-RGBA byte buffer at a
given pixel size, for consumption by PIL inside `drm_composer/painter.py`.

---

## Package layout

```
drm_resvg/
  Cargo.toml
  pyproject.toml
  src/
    lib.rs
  drm_resvg/
    __init__.py
  tests/
    test_render.py
```

---

## `Cargo.toml`

```toml
[package]
name = "drm_resvg"
version = "0.1.0"
edition = "2021"

[lib]
name = "drm_resvg"
crate-type = ["cdylib"]

[dependencies]
pyo3      = { version = "0.21", features = ["extension-module"] }
resvg     = "0.44"
usvg      = "0.44"
tiny-skia = "0.11"
```

---

## `pyproject.toml`

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

---

## `src/lib.rs`

```rust
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Rasterize an SVG document to straight RGBA at the given pixel size.
///
/// Returns (rgba_bytes, width, height).
/// tiny-skia produces premultiplied RGBA; this function converts to straight
/// RGBA before returning, so PIL / Pillow can use the bytes directly.
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
            width  as f32 / tree.size().width(),
            height as f32 / tree.size().height(),
        ),
        &mut pixmap.as_mut(),
    );

    // Convert premultiplied RGBA → straight RGBA
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

---

## `drm_resvg/__init__.py`

```python
from .drm_resvg import render_svg_rgba

__all__ = ["render_svg_rgba"]
```

---

## Public API

```python
rgba_bytes, width, height = render_svg_rgba(
    svg_bytes: bytes,   # raw SVG file contents
    width:     int,     # desired raster width in pixels
    height:    int,     # desired raster height in pixels
) -> (bytes, int, int)
```

- Input: raw SVG bytes (any valid SVG 1.1 / 2.0 document)
- Output: `width × height × 4` bytes, straight RGBA, row-major
- Raises `ValueError` if the SVG cannot be parsed or `width`/`height` is zero
- Does **not** preserve aspect ratio — caller is responsible for sizing
  (in `drm_composer`, `_fit_image` handles that downstream)

---

## Build and install

```bash
# Development install (requires Rust toolchain + maturin)
pip install maturin
pip install -e ./drm_resvg

# Or via pip from PyPI once published:
pip install drm-resvg
```

---

## Unit tests (`tests/test_render.py`)

```python
from drm_resvg import render_svg_rgba

RED_SQUARE = b"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <rect width="10" height="10" fill="red"/>
</svg>
"""

def test_output_shape():
    data, w, h = render_svg_rgba(RED_SQUARE, 20, 20)
    assert (w, h) == (20, 20)
    assert len(data) == 20 * 20 * 4

def test_red_square_pixels():
    data, w, h = render_svg_rgba(RED_SQUARE, 10, 10)
    # centre pixel should be opaque red
    i = (5 * w + 5) * 4
    r, g, b, a = data[i], data[i+1], data[i+2], data[i+3]
    assert r == 255 and g == 0 and b == 0 and a == 255

def test_straight_alpha():
    # a 50%-transparent blue square
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">
      <rect width="4" height="4" fill="rgba(0,0,255,0.5)"/>
    </svg>"""
    data, w, h = render_svg_rgba(svg, 4, 4)
    i = (2 * w + 2) * 4
    r, g, b, a = data[i], data[i+1], data[i+2], data[i+3]
    # straight alpha: blue channel should be ~255, not ~128 (premultiplied)
    assert b > 200 and a < 200

def test_bad_svg_raises():
    import pytest
    with pytest.raises(ValueError):
        render_svg_rgba(b"not svg", 10, 10)

def test_zero_size_raises():
    import pytest
    with pytest.raises(ValueError):
        render_svg_rgba(RED_SQUARE, 0, 0)
```
