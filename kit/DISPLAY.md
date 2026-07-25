# Choosing and configuring the panel

**Read this before you buy a display.**  One requirement decides whether a panel
works with this stack at all, and it is not printed on the box.

## The one requirement: it must be a DRM panel

`drm_display` renders by allocating a *dumb buffer* on a DRM/KMS device
(`/dev/dri/card0` or `card1`), pushing XRGB8888 pixels into it, and doing a
modeset. So:

```bash
ls /dev/dri/          # you need a cardN here — no cardN, no picture
```

Two consequences worth knowing up front:

**A `/dev/fb1` panel needs a patch first.**  Plenty of cheap SPI displays are
driven by the legacy `fbtft` stack (overlays named `piscreen`, `waveshare35a`,
`tft35a`, `flexfb`…), which gives you `/dev/fb1` and no DRM device.  Stock
`drm_display` cannot use it: the device list in `screen.py` is hardcoded to
`card0 → card1 → /dev/fb0 → dummy`, so `fb1` is never tried, and the framebuffer
backend memory-maps assuming **4 bytes per pixel** while an fbtft panel is
almost always 16-bit RGB565.

[`patches/drm_display-fbdev.patch`](patches/README.md) fixes both, plus RGB565
packing and line stride, and it is applied for you by:

```bash
./kit/install.sh --patch-fbdev
```

With it, an fbtft SPI panel is a supported option and you can skip the
`mipi-dbi-spi` route below entirely.  Without it, the row in the table stands.

While you are here, the same patch removes a trap worth knowing about even on an
HDMI build: unpatched, `FBDisplay` swallows every error, so a *missing*
framebuffer falls back to a 1920x1080 array in RAM and reports
`Display: /dev/fb0 (1920x1080)` while rendering nowhere.  **That exact line —
`fb0` at 1920x1080 — means nothing is connected.**

**No compositor may be running.**  `drm_display` never calls `drmSetMaster`; it
relies on being the only DRM client.  If X11, Wayland, or a desktop session holds
the master lock, your writes are **silently ignored** — a black screen with no
error anywhere.  Install **Raspberry Pi OS Lite** and this never comes up.

## What works

| Panel family | Config | DRM? | Notes |
|---|---|---|---|
| **HDMI** (5"/7" with an HDMI board) | `dtoverlay=vc4-kms-v3d` | ✅ | The safe choice. Works out of the box; a mini-HDMI adapter is all the Zero needs. |
| **DSI** (official 7" touch display) | `dtoverlay=vc4-kms-dsi-7inch` | ✅ | Best picture, includes touch over I²C. Ribbon only, no GPIO. |
| **DPI** (parallel RGB — Hyperpixel and similar) | vendor overlay, or `vc4-kms-dpi-generic` + timings | ✅ | Eats ~28 GPIOs. Timings are panel-specific — use the vendor's overlay if there is one. |
| **SPI via a DRM driver** (`mipi-dbi-spi`, or a `drm/tiny` driver) | see below | ✅ | The route for a 3.5" SPI panel. Slow bus, but this game repaints small regions. |
| **SPI via fbtft** (`piscreen`, `waveshare35a`, …) | vendor overlay | ❌ → ✅ | `/dev/fb1` at 16 bpp: needs `install.sh --patch-fbdev` ([why](patches/README.md)). Easiest route for a cheap 3.5". |

Nothing about the *hardware* differs between the last two rows.  The same 3.5"
ILI9486 panel is a DRM device or a framebuffer depending on which driver you bind
it to — and with the fbdev patch, either one works.  Pick whichever your board's
vendor overlay already does, which for most cheap 3.5" panels is `fbtft`.

## Identify what you have

On a booted Pi:

```bash
ls /dev/dri /dev/fb*                 # cardN = DRM, fb1 = legacy fbtft
drm-list-modes                       # (installed by install.sh) devices,
                                     # connectors, modes, and who holds master
```

`drm-list-modes` names the connector type, which tells you which row of the table
you are in: `HDMI-A`, `DSI`, `DPI`, or `SPI`.  It exits non-zero when it cannot
find anything usable, and it reports the master lock — so it answers both "is
there a panel" and "is something else holding it".

## Getting an SPI panel onto DRM

If you have a 3.5" SPI panel and want it as a DRM device, the mechanism is the
kernel's **`panel-mipi-dbi-spi`** driver, via `dtoverlay=mipi-dbi-spi`.  It needs
two things:

1. **Overlay parameters** for your wiring — which SPI device, and the reset, D/C
   and backlight GPIOs.
2. **A panel description file** in `/boot/firmware/`, compiled from the panel's
   init sequence with the `mipi-dbi-cmd` tool from `raspberrypi/utils`.  The
   controller (ILI9486, ILI9341, ST7789…) and the panel's own datasheet decide
   its contents.

I am deliberately not printing an overlay line for you to paste: the parameter
names and the panel file are version- and panel-specific, and a wrong line in
`config.txt` is a Pi that does not boot to a display.  **The authority is on the
card you just flashed** — read it there, where it matches your OS version:

```bash
grep -A 40 "mipi-dbi-spi" /boot/firmware/overlays/README
```

plus the *SPI displays* section of the official Raspberry Pi documentation.  For
the same reason `install.sh` refuses to guess: `--display` only writes config it
can stand behind (`hdmi`, `dsi7`), and for DPI/SPI it leaves `config.txt` alone
and tells you to come here.

**Then verify, don't assume:**

```bash
ls /dev/dri/          # a new cardN appeared?
./kit/verify.sh       # end-to-end: DRM device, C helper, groups, touch, benchmark
```

If a `cardN` appears and `verify.sh` is happy, you are done — the stack does not
care what kind of panel it is from that point on.

## Touch

Touch is independent of the display path.  `drm_touch` reads **evdev**
(`/dev/input/event*`), so anything the kernel exposes as an input device works:

| Touch type | Config | Check |
|---|---|---|
| Resistive (XPT2046 / ADS7846) — typical on 3.5" SPI boards | `dtoverlay=ads7846,...` (parameters in the overlays README) | `python -m drm_touch` |
| Capacitive USB (most HDMI panels) | none — USB HID | `python -m drm_touch` |
| Official DSI 7" | comes with `vc4-kms-dsi-7inch` | `python -m drm_touch` |

```bash
python -m drm_touch            # list input devices and show which one it picks
python -m drm_touch --watch    # print live events while you prod the panel
```

Your user must be in the **`input`** group (`install.sh` does this).  Resistive
panels usually need calibrating and can come up mirrored or with axes swapped —
`drm_touch` owns that mapping; see its README.  A quick sanity check: tap the
top-left cell of the board and confirm the highlight lands there, not in another
corner.

## Cosmetic polish (optional)

To keep boot text off the panel so the game is the only thing that ever appears,
in `/boot/firmware/cmdline.txt` (one line, space-separated):

```
quiet logo.nologo vt.global_cursor_default=0 consoleblank=0
```

and if your HDMI panel has no usable EDID, force the mode there too:

```
video=HDMI-A-1:800x480@60
```
