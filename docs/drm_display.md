# drm_display — Implementation Instructions

Changes required across the roadmap stages that affect this package.
Covers Stage 5 (hardware cursor) only — Stage 1–4 do not touch this package.

---

## Stage 5 — Hardware cursor: C-level additions

**Goal:** expose `drmModeSetCursor2` and `drmModeMoveCursor` as Python methods
on `Screen`, so `drm_screen` can move the cursor via a DRM plane update instead
of a framebuffer rewrite.

`drm_screen` owns the feature logic; this package provides only the thin ioctl
wrappers. No atomic KMS or plane enumeration is required.

---

### Background (verified against source)

`drm_display/drm_display.c` currently uses only `drmModeSetCrtc` (lines 112–185).
The C code holds the DRM file descriptor (`fd`) and `crtc_id` — `drm_screen` has
no access to these, so cursor ioctls must be exposed here.

---

### New C additions to `drm_display/drm_display.c`

#### 1. GEM dumb buffer for the cursor bitmap

Allocate a kernel-managed buffer for the cursor image using
`DRM_IOCTL_MODE_CREATE_DUMB` and map it with `mmap`:

```c
static int create_cursor_bo(int fd, uint32_t width, uint32_t height,
                             uint32_t *handle_out, uint32_t *pitch_out,
                             void **map_out, uint64_t *map_size_out)
{
    struct drm_mode_create_dumb cd = {
        .width  = width,
        .height = height,
        .bpp    = 32,
    };
    if (ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd) < 0)
        return -errno;

    struct drm_mode_map_dumb md = { .handle = cd.handle };
    if (ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &md) < 0)
        return -errno;

    void *map = mmap(NULL, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                     fd, md.offset);
    if (map == MAP_FAILED)
        return -errno;

    *handle_out   = cd.handle;
    *pitch_out    = cd.pitch;
    *map_out      = map;
    *map_size_out = cd.size;
    return 0;
}
```

Store `handle`, `map`, and `map_size` on the display state struct so the buffer
can be reused across `set_cursor` calls and freed on `close()`.

#### 2. `set_cursor` — upload bitmap + set hotspot

```c
static PyObject *py_set_cursor(PyObject *self, PyObject *args)
{
    const uint8_t *rgba;
    Py_ssize_t     data_len;
    int width, height, hotspot_x, hotspot_y;

    if (!PyArg_ParseTuple(args, "y#iiii",
                          &rgba, &data_len, &width, &height,
                          &hotspot_x, &hotspot_y))
        return NULL;

    DisplayState *s = get_state(self);   /* your existing state accessor */

    /* (Re)allocate cursor BO if size changed */
    if (s->cursor_width != width || s->cursor_height != height) {
        /* destroy old BO if any ... */
        if (create_cursor_bo(s->fd, width, height, &s->cursor_handle,
                             &s->cursor_pitch, &s->cursor_map,
                             &s->cursor_map_size) < 0)
            return PyErr_SetFromErrno(PyExc_OSError);
        s->cursor_width  = width;
        s->cursor_height = height;
    }

    /* Convert straight RGBA → ARGB8888 (DRM cursor plane format) and copy */
    uint32_t *dst = (uint32_t *)s->cursor_map;
    for (int i = 0; i < width * height; i++) {
        uint8_t r = rgba[i*4+0], g = rgba[i*4+1],
                b = rgba[i*4+2], a = rgba[i*4+3];
        dst[i] = ((uint32_t)a << 24) | ((uint32_t)r << 16)
               | ((uint32_t)g <<  8) |  (uint32_t)b;
    }

    if (drmModeSetCursor2(s->fd, s->crtc_id, s->cursor_handle,
                          width, height, hotspot_x, hotspot_y) < 0)
        return PyErr_SetFromErrno(PyExc_OSError);

    Py_RETURN_NONE;
}
```

#### 3. `move_cursor` — plane update, no buffer re-upload

```c
static PyObject *py_move_cursor(PyObject *self, PyObject *args)
{
    int x, y;
    if (!PyArg_ParseTuple(args, "ii", &x, &y))
        return NULL;

    DisplayState *s = get_state(self);
    if (drmModeMoveCursor(s->fd, s->crtc_id, x, y) < 0)
        return PyErr_SetFromErrno(PyExc_OSError);

    Py_RETURN_NONE;
}
```

#### 4. `has_hardware_cursor` property

Add a simple boolean property that indicates whether the device supports the
cursor plane. The simplest reliable check is to attempt a zero-size
`drmModeSetCursor2` probe at init time, or check for the `DRM_CAP_CURSOR_WIDTH`
capability:

```c
static PyObject *py_has_hardware_cursor(PyObject *self, void *closure)
{
    DisplayState *s = get_state(self);
    uint64_t val = 0;
    if (drmGetCap(s->fd, DRM_CAP_CURSOR_WIDTH, &val) == 0 && val > 0)
        Py_RETURN_TRUE;
    Py_RETURN_FALSE;
}
```

Register as a `PyGetSetDef` on the `Screen` type.

#### 5. Register in the method table

```c
static PyMethodDef Screen_methods[] = {
    /* ... existing methods ... */
    {"set_cursor",  py_set_cursor,  METH_VARARGS, "Upload cursor bitmap."},
    {"move_cursor", py_move_cursor, METH_VARARGS, "Move cursor plane."},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef Screen_getsets[] = {
    /* ... existing ... */
    {"has_hardware_cursor", py_has_hardware_cursor, NULL,
     "True if the DRM device supports a cursor plane.", NULL},
    {NULL}
};
```

---

### Dummy backend (`drm_display/db_display.py` or equivalent)

The headless dummy backend used in tests must also expose the new interface, so
the fallback path in `drm_screen` works without real hardware:

```python
class DummyScreen:
    has_hardware_cursor = False   # always SW fallback in tests

    def set_cursor(self, rgba, width, height, hotspot_x=0, hotspot_y=0):
        pass   # no-op

    def move_cursor(self, x, y):
        pass   # no-op
```

---

### Cleanup

In the `Screen` close/dealloc path, destroy the cursor GEM buffer:

```c
if (s->cursor_handle) {
    munmap(s->cursor_map, s->cursor_map_size);
    struct drm_mode_destroy_dumb dd = { .handle = s->cursor_handle };
    ioctl(s->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dd);
    s->cursor_handle = 0;
}
```

---

### What does NOT change

- `show()`, `copy()`, `get_screen_size()`, `close()` — unchanged.
- The RGBA→BGRA conversion stays in `drm_screen/backend.py` — not here.
- No changes to `drm_display/__init__.py` public API beyond the new methods
  on `Screen`.
