# drm_touch — Implementation Instructions

Changes required across the roadmap stages that affect this package.
Only Stage 5 (hardware cursor) requires a change here — and it is a small,
backward-compatible extension to one function.

---

## Stage 5 — Hardware cursor: `fan_out` extension

**One file changes:** `drm_touch/reader.py`

### Background (verified against source)

`fan_out` currently has the signature (line 44):

```python
def fan_out(submit, app_queue, cursor=True, hide_on_release=False):
```

Inside, it emits `SetPointer(ev.x, ev.y, visible=visible)` (line 61) for every
pointer event. When hardware cursor is active, the application needs to emit
`MoveHardwareCursor(x, y)` instead.

### Change

Add an optional `cursor_command_factory` parameter. When provided, its return
value is submitted instead of `SetPointer`. Default is `None`, which preserves
exactly the current behavior:

```python
def fan_out(submit, app_queue, cursor=True, hide_on_release=False,
            cursor_command_factory=None):   # NEW — default: use SetPointer
    from drm_screen.commands import SetPointer

    def sink(ev):
        visible = not (hide_on_release and ev.phase == "up")
        if cursor:
            if cursor_command_factory is not None:
                submit([cursor_command_factory(ev.x, ev.y)])
            else:
                submit([SetPointer(ev.x, ev.y, visible=visible)])
        app_queue.put(ev)

    return sink
```

### Usage (application code, not drm_touch itself)

An application that wants hardware cursor movement:

```python
from drm_screen.commands import MoveHardwareCursor
from drm_touch import fan_out

sink = fan_out(
    service.submit,
    app_queue,
    cursor_command_factory=lambda x, y: MoveHardwareCursor(x, y),
)
```

An application that uses the default software cursor needs no changes at all.

---

## What does NOT change

- `TouchEvent`, `TouchReader`, `DummyTouch`, `find_pointer_source` — unchanged.
- `fan_out` with no `cursor_command_factory` is **bit-identical** in behavior to
  the current version.
- `drm_touch/__init__.py` exports — no change needed (the parameter is optional).
