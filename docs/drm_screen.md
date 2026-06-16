# drm_screen — Implementation Instructions

Changes required across the roadmap stages that affect this package.
Covers Stage 2 (cursor split), Stage 4 (Rust compositor), and Stage 5
(hardware cursor). Each stage is independent and can be implemented separately.

---

## Stage 2 — Cursor architecture split (base_frame / front_frame)

**Goal:** cursor moves must not trigger a full scene recomposite.

### Background (verified against source)

- `ScreenService.dirty` already exists (`service.py:23`). It is set to `True`
  in `_drain()` (`service.py:49`) after every batch — including cursor moves.
- `Composer.render()` (`composer.py:51`) rebuilds the full canvas on every call
  when `dirty` is set — there is no caching today.
- Cursor moves arrive as `SetPointer(x, y, visible)` (`commands.py:97`), handled
  at `apply_command` line 161–171.

### Changes to `drm_screen/composer.py`

Add three new attributes to `Composer.__init__` (note: real attribute names are
`screen_width` / `screen_height`, not `width` / `height`):

```python
def __init__(self, screen_width: int, screen_height: int):
    self.screen_width = screen_width
    self.screen_height = screen_height
    self.layers: dict[str, Layer] = {}
    # NEW:
    self._base_frame: np.ndarray | None = None
    self._base_dirty: bool = True       # rebuild when any non-cursor layer changes
    self._render_count: int = 0         # test instrumentation only
```

Replace `render()` (lines 51–59) with a two-phase version that reuses the
existing `_blend` staticmethod:

```python
def render(self) -> np.ndarray:
    from .commands import _POINTER_NAME
    # Phase 1: rebuild base only when the scene changed
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

    # Phase 2: blit cursor onto a copy — cheap regardless of scene complexity
    cursor = self.layers.get(_POINTER_NAME)
    if cursor and cursor.visible and cursor.buffer is not None:
        front = self._base_frame.copy()
        self._blend(front, cursor)
        return front
    return self._base_frame
```

### Changes to `drm_screen/commands.py`

At the end of `apply_command(composer, cmd)` (line 134), set `_base_dirty` for
every command that is not a pure cursor move:

```python
def apply_command(composer: Composer, cmd) -> None:
    # ... existing dispatch (unchanged) ...

    # NEW: mark base dirty unless this was a cursor-only move
    # (SetPointer that updates an existing cursor layer doesn't change the scene)
    if not (isinstance(cmd, SetPointer) and _POINTER_NAME in composer.layers):
        composer._base_dirty = True
```

**Why this condition:** the first `SetPointer` (which creates the cursor layer)
must set `_base_dirty = True` — the new layer changes the `layers` dict. Only
subsequent `SetPointer` calls on an already-existing cursor layer are pure moves
that skip the rebuild. The condition captures exactly that.

### What does NOT change

- `ScreenService.dirty` — still governs `render_once()`. It remains separate
  from `_base_dirty`; the two flags are orthogonal.
- `DrmDisplayBackend`, `backend.py` — no change.
- Public API (`submit`, `hit_test`, `render_once`) — no change.
- All existing tests pass unchanged.

---

## Stage 4 — Rust compositor (`drm_screen_native`)

**Goal:** move `Composer.render()` to a Rust/PyO3 extension for throughput on
large displays. `drm_screen_native` is a separate package; see
[`drm_screen_native.md`](drm_screen_native.md) for its implementation.

### Changes to `drm_screen/composer.py`

Add a try/except at module level:

```python
try:
    from drm_screen_native import render_layers as _native_render
    _HAVE_NATIVE = True
except ImportError:
    _HAVE_NATIVE = False
```

Add `_render_native()` and split `render()` to dispatch between paths.
The base/front split from Stage 2 is preserved in both paths:

```python
def render(self) -> np.ndarray:
    if _HAVE_NATIVE:
        return self._render_native()
    return self._render_python()

def _render_python(self) -> np.ndarray:
    # The Stage 2 two-phase implementation (base_frame + cursor blit)
    ...

def _render_native(self) -> np.ndarray:
    from .commands import _POINTER_NAME
    # Same two-phase logic but delegates the base blend to Rust.
    # Layer pixels live in layer.buffer (numpy ndarray), not bytes.
    # opacity must be passed — the Python blend uses it as a float32 multiplier.
    if self._base_dirty or self._base_frame is None:
        frame_bytes = _native_render(
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
                    "data": layer.buffer.tobytes(),
                }
                for layer in sorted(self.layers.values(), key=lambda l: l.z)
                if layer.visible
                and layer.buffer is not None
                and layer.name != _POINTER_NAME
            ],
        )
        self._base_frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
            self.screen_height, self.screen_width, 4
        ).copy()
        self._base_dirty = False
        self._render_count += 1

    cursor = self.layers.get(_POINTER_NAME)
    if cursor and cursor.visible and cursor.buffer is not None:
        front = self._base_frame.copy()
        self._blend(front, cursor)
        return front
    return self._base_frame
```

**Parity note:** the Python blend (`_blend`, line 72–78) uses float32 arithmetic.
A Rust integer blend will be off by ±1 LSB per channel. The parity integration
test should use `np.testing.assert_allclose(atol=1)`, not `assert_array_equal`.

---

## Stage 5 — Hardware cursor

**Goal:** cursor moves become a DRM plane update instead of a framebuffer rewrite.
`drm_screen` owns the feature logic; `drm_display` provides thin C bindings (see
[`drm_display.md`](drm_display.md)).

### New commands in `drm_screen/commands.py`

Add two new command dataclasses. The `data` field name is intentional — `to_wire`
and `from_wire` already base64-encode any field named `data` (line 187), so wire
serialization works without changes to those functions.

```python
@dataclass
class SetHardwareCursor:
    """Upload cursor bitmap to the DRM cursor plane."""
    data: bytes          # width*height*4 straight RGBA
    width: int
    height: int
    hotspot_x: int = 0
    hotspot_y: int = 0

@dataclass
class MoveHardwareCursor:
    """Move the cursor plane atomically — no recomposite."""
    x: int
    y: int
```

Register both in `_KINDS` (line 178) so the wire format round-trips:

```python
_KINDS = {c.__name__: c for c in (
    CreateLayer, DeleteLayer, ClearLayer, ShowLayer, HideLayer,
    SetPosition, SetZ, PlaceRawBuffer, SetInteractive, SetPointer,
    SetHardwareCursor, MoveHardwareCursor,   # NEW
)}
```

Handle them in `apply_command`. These two commands need access to the backend
(not just the composer), so handle them in `ScreenService.render_once()` /
`_drain()` before `apply_command` runs, or thread the backend into the apply
path. Simplest approach — a backend-aware pre-filter in `_drain()`:

```python
# drm_screen/service.py — inside _drain(), before the apply_command call
for cmd in batch:
    if isinstance(cmd, SetHardwareCursor):
        if self.backend.has_hardware_cursor:
            self.backend.set_cursor(
                cmd.data, cmd.width, cmd.height,
                cmd.hotspot_x, cmd.hotspot_y,
            )
        # whether HW or SW, mark base dirty (cursor layer may be newly created)
        apply_command(self.composer, cmd)   # no-op if HW path taken
    elif isinstance(cmd, MoveHardwareCursor):
        if self.backend.has_hardware_cursor:
            self.backend.move_cursor(cmd.x, cmd.y)
            # no composer change, no dirty flag — plane moved in hardware
        else:
            # SW fallback: translate to SetPointer
            apply_command(self.composer, SetPointer(cmd.x, cmd.y, visible=True))
        self.dirty = True
    else:
        apply_command(self.composer, cmd)
        self.dirty = True
```

### Changes to `drm_screen/backend.py`

Add capability query and two wrapper methods:

```python
@property
def has_hardware_cursor(self) -> bool:
    return getattr(self.screen, "has_hardware_cursor", False)

def set_cursor(self, rgba: bytes, width: int, height: int,
               hotspot_x: int = 0, hotspot_y: int = 0) -> None:
    self.screen.set_cursor(rgba, width, height, hotspot_x, hotspot_y)

def move_cursor(self, x: int, y: int) -> None:
    self.screen.move_cursor(x, y)
```

### Changes to `drm_screen/service.py`

In `start()`, detect hardware cursor and upload the default bitmap if available.
`default_cursor()` and `_cursor_hotspot()` already exist in `commands.py`:

```python
def start(self) -> None:
    if self._thread is not None:
        return
    self._using_hw_cursor = False
    if self.backend.has_hardware_cursor:
        from .commands import default_cursor, _cursor_hotspot
        cur = default_cursor()          # ndarray (h, w, 4) RGBA
        ch, cw = cur.shape[:2]
        hx, hy = _cursor_hotspot()
        self.backend.set_cursor(cur.tobytes(), cw, ch, hx, hy)
        self._using_hw_cursor = True
    self._thread = threading.Thread(target=self._run, daemon=True)
    self._thread.start()
```

### What does NOT change

- `Composer`, `Layer`, `assets.py`, `target.py` — no changes.
- `fan_out` in `drm_touch` has a new optional parameter but its default behavior
  is unchanged — no forced update to application code.
- The software cursor path (`SetPointer`, `default_cursor`, `_POINTER_NAME`)
  remains fully intact as the fallback.

---

## Integration tests (drm_stack umbrella)

| File | Stage | Key assertions |
|---|---|---|
| `integration/test_cursor_split.py` | 2 | Cursor move does not set `_base_dirty`; non-cursor layer change does; `_render_count` increments only on scene change |
| `integration/test_native_compositor.py` | 4 | Native and Python frames agree within `atol=1` |
| `integration/test_hw_cursor.py` | 5 | `SetHardwareCursor`/`MoveHardwareCursor` accepted without error on dummy backend (SW fallback); `MoveHardwareCursor` falls back to `SetPointer` behavior; `fan_out` with factory emits custom commands |
