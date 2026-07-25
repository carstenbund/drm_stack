# Patches

Changes to the packages that live in their own repos, kept here so a kit build
can apply them before they land upstream.

## `drm_display-fbdev.patch`

**Makes an SPI panel on `/dev/fb1` work**, so a cheap 3.5" TFT under `fbtft`
becomes a first-class option and the `mipi-dbi-spi` panel-blob route in
[../DISPLAY.md](../DISPLAY.md) stops being the only way in.

Four changes to `drm_display`:

1. **`/dev/fbN`, not just `fb0`.**  `screen.py`'s candidate list was the literal
   tuple `card0 → card1 → /dev/fb0 → dummy`, and a device outside it was skipped
   — so `Screen(device="/dev/fb1")` raised *No suitable display backend found*.
   Auto-detection now enumerates `/dev/dri/card*` and `/dev/fb*`, and a named
   device is dispatched by path.
2. **Real geometry and depth.**  `fb_display.py` reads `virtual_size`,
   `bits_per_pixel`, `stride` and `name` from sysfs instead of assuming
   32 bits per pixel, and honours the **stride**, so padded lines land correctly.
3. **RGB565.**  A BGRA canvas is packed to 16bpp (and 24bpp) for the panel.  A
   16-bit SPI TFT is the common case and was previously unreachable.
4. **It stops pretending.**  This is the important one.  `FBDisplay` swallowed
   every error: a missing device fell back to a 1920x1080 *in-RAM* array, so the
   log read `Display: /dev/fb0 (1920x1080)`, the app ran happily, and every
   pixel went nowhere.  Now the constructor raises, a named device is never
   silently replaced by a fallback, and auto-detection prints a loud warning
   when it lands on the headless backend.  `DBDisplay` stays the only backend
   that always succeeds, and you have to ask for it by name.

Adds `tests/test_fb_display.py` — 21 tests covering packing at all three
depths, stride padding, clipping, partial updates, device dispatch, and each
refusal above.  No hardware needed.

### Apply

`install.sh --patch-fbdev` does this for you.  By hand:

```bash
cd ~/drm_stack/drm_display
git apply ../kit/patches/drm_display-fbdev.patch
../.venv/bin/python -m pytest tests/test_fb_display.py -q     # 21 passed
```

Then point the stack at the panel — auto-detection will find it, or be explicit:

```bash
.venv/bin/python integration/sudoku_demo.py --device /dev/fb1
```

### Status

Written and tested against `drm_display` at `5c732af`, applies cleanly to a
pristine checkout, and **not upstream** — it belongs as a PR on
[`carstenbund/drm_display`](https://github.com/carstenbund/drm_display).  Until
then this file is the source of truth.

Untested on a real 16bpp panel: the byte layout is verified against a fixture
framebuffer, but nobody has yet watched it light up an ILI9486.  If you are the
first, the `__main__` block in `fb_display.py` paints the screen red —
`python -m drm_display.fb_display /dev/fb1`.  A blue screen means the channel
order is wrong somewhere.
