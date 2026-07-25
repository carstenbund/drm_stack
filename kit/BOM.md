# Bill of materials — Sudoku kit

Prices are **indicative** street prices (early 2026, excl. VAT and shipping) to
help you budget, not quotes.  Everything is stock, widely-cloned hardware.

## Core — one of these three builds

| # | Build | Board | Panel | Total (indicative) |
|---|---|---|---|---|
| A | **Pocket** (recommended first build) | Pi Zero 2 W | 3.5" SPI, 480x320 | ~€45 |
| B | **Desk** | Pi Zero 2 W | 5" HDMI, 800x480 | ~€60 |
| C | **Tabletop** | Pi Zero 2 W or Pi 3/4 | 7" DSI, 1024x600 | ~€95 |

Build A is what the photo in this thread is: a Zero riding on a 3.5" driver
board.  It is the cheapest, the most compact, and the fastest of the three to
render — a 480x320 frame is a quarter of the pixels of 1024x600, and this stack
composites in Python, so pixel count is what you feel.  See
[Performance](#performance) before you pick a bigger panel.

## Parts

| Qty | Part | Notes | ~€ |
|---|---|---|---|
| 1 | **Raspberry Pi Zero 2 W** | Quad-core, and the Wi-Fi is what makes the remote install a one-liner.  A Zero W works; see *board notes*. | 18 |
| 1 | **Display** | 3.5" / 5" / 7" — **must present as a DRM device**, see [DISPLAY.md](DISPLAY.md).  This is the one spec that can sink the build. | 14–70 |
| 1 | **microSD card, 16 GB, A1** | 8 GB is enough for Pi OS Lite; 16 GB costs the same. Class A1 or better — the cheap ones are where "it boots slowly" comes from. | 7 |
| 1 | **USB power supply, 5 V ≥ 2 A, micro-USB** | A phone charger works.  Under-powered supplies cause resets under load, which look exactly like software bugs. | 8 |
| 1 | **2×20 male header, 0.1" (2.54 mm)** | The only soldering in the kit.  Buy a strip of 5 — spares cost nothing and the first one is practice. | 1 |
| 1 | **M2.5 standoff + screw set** | 4 × 6–11 mm.  Most 3.5" boards ship with them; check before ordering. | 3 |
| 1 | **microSD → SD adapter or USB reader** | For flashing, if your laptop has no slot. | 5 |

### Only if your build needs them

| Qty | Part | When | ~€ |
|---|---|---|---|
| 1 | **mini-HDMI → HDMI adapter/cable** | Build B (the Zero's HDMI port is mini). | 4 |
| 1 | **micro-USB OTG → USB-A adapter** | Build B: capacitive touch panels are USB HID.  Also the escape hatch for a keyboard. | 3 |
| 1 | **USB-Ethernet or USB-Wi-Fi dongle + OTG adapter** | Only for a **Pi Zero (non-W)** — it has no network, and the install needs one.  See *board notes*. | 10 |
| 1 | **Pre-soldered "Pi Zero WH"** | Buy instead of the header if you would rather not solder at all.  Adds ~€3 and removes [ASSEMBLY.md](ASSEMBLY.md) step 1. | +3 |

### Tools

Soldering iron with a fine conical or 1.6 mm chisel tip (a temperature-controlled
one at 320–350 °C makes this easy; a 25 W fixed pencil also works), 0.7 mm
solder, flush cutters, tweezers, a multimeter with a continuity beeper, isopropyl
alcohol and a brush for flux, and a damp sponge or brass wool.  A helping-hands
clamp is worth more than it costs for a 40-pin header.

## Board notes

**Pi Zero 2 W** is the recommendation for two unrelated reasons: it is roughly
4–5× faster than the original Zero at the array work this stack does, and it has
Wi-Fi, so Raspberry Pi Imager can pre-seed the network and the whole install is
one SSH command.

**Pi Zero W** — fine.  Slower; same install flow.

**Pi Zero (non-W)** — the game runs, but there is no network, so *installing* is
the awkward part.  Three ways out, cheapest first:

1. USB-OTG ethernet gadget: add `dtoverlay=dwc2` to `config.txt` and
   `modules-load=dwc2,g_ether` to `cmdline.txt`, plug the **USB** (not PWR) port
   into a computer, and share that computer's connection with the new interface.
2. A USB-Wi-Fi or USB-Ethernet dongle on an OTG adapter (~€10).
3. Install onto the SD from another Pi, or flash a golden image someone else
   built (see [README.md](README.md#making-a-golden-image)).

If you are assembling several kits, option 3 is the answer — build one, image it,
and every other card is a copy.

## Performance

This stack composites frames in NumPy on the CPU, so a move costs roughly one
full-frame blend.  Measured on the development machine (x86, not a Pi):

| Panel | Compile | Composite | Total per move |
|---|---|---|---|
| 480x320 (3.5") | 11 ms | 27 ms | **38 ms** |
| 800x480 (5") | 12 ms | 58 ms | **73 ms** |
| 1024x600 (7") | 13 ms | 93 ms | **107 ms** |

A Pi Zero 2 W will be several times slower than that, and an original Zero
several times slower again — I have not measured on either, so rather than guess,
measure yours:

```bash
.venv/bin/python integration/sudoku_demo.py --benchmark --width 480 --height 320
```

It runs headless (safe over SSH) and prints the per-move cost with a verdict.
Sudoku is turn-based, so a tap costing 200–300 ms still feels fine; if your
number is worse than that, the levers are, in order: a smaller panel, a Zero 2 W
instead of a Zero, or a Pi 3/4.
