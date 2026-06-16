# drm_composer — Implementation Instructions

Changes required across the roadmap stages that affect this package.

---

## Stage 1 — SVG asset support

**One file changes:** `drm_composer/painter.py`

### Add `drm-resvg` as a dependency

`drm_composer/setup.py` — add to `install_requires`:

```python
"drm-resvg",   # optional: absent → SVG falls back to placeholder
```

### Modify `_paste_image(canvas, node)` (line 115)

Current code at line 122 opens images unconditionally with PIL:

```python
try:
    img = Image.open(node.src).convert("RGBA")
except OSError:
    _paste_placeholder(canvas, x, y, w, h, node.src)
    return
```

Replace with an extension check before the PIL path:

```python
import os
ext = os.path.splitext(node.src)[1].lower()
if ext == ".svg":
    try:
        from drm_resvg import render_svg_rgba
        # SVG has no implicit pixel size; use explicit w/h if given,
        # else the canvas size.
        rw, rh = (w or canvas.width), (h or canvas.height)
        with open(node.src, "rb") as f:
            rgba_bytes, rw, rh = render_svg_rgba(f.read(), rw, rh)
        img = Image.frombytes("RGBA", (rw, rh), rgba_bytes)
    except Exception:       # parse error, missing file, or drm_resvg absent
        _paste_placeholder(canvas, x, y, w, h, node.src)
        return
else:
    try:
        img = Image.open(node.src).convert("RGBA")
    except OSError:
        _paste_placeholder(canvas, x, y, w, h, node.src)
        return
```

**Notes:**
- `w`/`h` may be `0` (image pasted at natural size). SVG has no intrinsic pixel
  size, so fall back to canvas dimensions in that case.
- Catching broad `Exception` is intentional: `drm_resvg` raises `ValueError` on
  bad SVG and `ImportError` if the package is not installed — both degrade
  gracefully to a placeholder, matching the existing `OSError` behavior for PNGs.
- The rest of `_paste_image` (the `_fit_image` call and `alpha_composite`) runs
  unchanged after this block.

### Optional: `<svg src="…" />` syntax sugar

`drm_composer/parser.py` — in the element dispatch table, add an alias:

```python
"svg": parse_img_node,   # treated identically to <img>
```

`parse_img_node` already extracts `src`, `w`, `h`, `fit` and emits an
`ImageNode`. No painter change needed — the SVG branch in `_paste_image` already
handles `.svg` regardless of which tag produced the node.

---

## What does NOT change

- The command contract (`CreateLayer`, `PlaceRawBuffer`) — unchanged.
- `drm_screen`, `drm_display`, `drm_touch` — untouched.
- `paint_scene()`, `parse_scene()`, `Compositor` — no changes to public API.

---

## Integration tests (drm_stack umbrella)

New file: `integration/test_svg.py`

Key assertions:
- `<img src="*.svg" />` compiles without error and produces a valid RGBA frame.
- `<svg src="*.svg" />` produces the same result (alias parity).
- Missing SVG shows the placeholder, does not raise.
- Absent `drm_resvg` package shows the placeholder, does not raise.

Test asset: `integration/assets/test.svg` — a minimal red-square SVG:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <rect width="10" height="10" fill="red"/>
</svg>
```
