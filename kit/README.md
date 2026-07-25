# Sudoku kit — a guided build

A Raspberry Pi Zero, a small touchscreen, and a playable sudoku that renders
straight to the panel.  No X11, no Wayland, no browser — the whole graphics
stack is the four `drm_*` packages and about 700 lines of Python.

One evening: solder a header, flash a card, run one command.

| | |
|---|---|
| **Build** | [BOM.md](BOM.md) — parts and three price points |
| **Solder** | [ASSEMBLY.md](ASSEMBLY.md) — the 40-pin header, and the checks |
| **Panel** | [DISPLAY.md](DISPLAY.md) — **read before buying a screen** |
| **Install** | [install.sh](install.sh) — one command on the Pi |
| **Debug** | [verify.sh](verify.sh) — "why is my screen black", in order |

## The flow

### 1. Solder and assemble

[ASSEMBLY.md](ASSEMBLY.md).  Header on the Zero, panel on top, continuity check
before anything is powered.  Skip entirely if you bought a **Pi Zero WH**.

### 2. Flash the card

**Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager.  Lite matters: a
desktop session would hold the DRM master lock and the game would render into
nothing, silently. In Imager, open the gear/**Edit settings** and set:

- hostname → `sudoku`
- username + password (the examples below assume `pi`)
- **Wi-Fi** SSID and password
- **Enable SSH** (password or key)

That last three are what make step 3 a single command instead of an afternoon
with a keyboard and a HDMI adapter.

### 3. Remote install

Boot the Pi, wait a minute, then from your laptop:

```bash
ssh pi@sudoku.local

curl -fsSL https://raw.githubusercontent.com/carstenbund/drm_stack/main/kit/install.sh \
  | bash -s -- --display hdmi
```

> Until this is merged to `main`, install from the branch — both the script and
> the checkout:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/carstenbund/drm_stack/claude/sudoku-game-demo-lj04jt/kit/install.sh \
>   | bash -s -- --branch claude/sudoku-game-demo-lj04jt --display hdmi
> ```

Use `--display hdmi` for an HDMI panel, `--display dsi7` for the official 7", and
`--display keep` for a DPI or SPI panel — those are panel-specific and the
installer will not guess at your `config.txt` ([DISPLAY.md](DISPLAY.md) has the
reasoning and the recipe). `./install.sh --help` lists the rest.

Then:

```bash
sudo reboot
```

### 4. Plug in and go

It comes back up playing sudoku.  Tap a cell, tap a digit on the right.

```bash
cd ~/drm_stack && ./kit/verify.sh      # if it doesn't
```

## Playing it

Tap a cell, then a digit.  Tap the same digit again to take it back out.
Clashes turn red at **both** ends, so you can see what you hit.  Each pad key
carries a nine-pip meter of how many of that digit are on the board, and goes
green when that digit is finished.  **Hint** reveals a cell — hold it down and it
solves the board a cell at a time.  Solve it and the board dims; tap it for a new
puzzle.

By hand, with the kiosk stopped:

```bash
sudo systemctl stop sudoku-kiosk
.venv/bin/python integration/sudoku_demo.py --difficulty hard
.venv/bin/python integration/sudoku_demo.py --benchmark     # how fast is this Pi
```

## What the build teaches

The demo is a worked example of the stack's layer model, and the hardware makes
it legible in a way a desktop never does:

- **Only the top layers are alive.**  The board is inert paint at z=0.  Every
  touchable thing is a `<button>`, which `drm_composer` lifts into its own
  interactive layer 1000 above its parent — 81 cell keys at z=1010, nine digit
  keys at z=1020, floating over a board that can never be hit.
- **Painting is proportional to what changed.**  Selecting a cell repaints no
  cell at all: the highlight is one cell-sized layer moved with `SetPosition`.
  On a Zero, where a full-frame blend is expensive, that is the difference
  between a responsive tap and a sluggish one.
- **z-order is the modal.**  Solving puts a scrim at z=3000 over the cell keys,
  so taps stop reaching the game because something is on top of them.

Read `integration/sudoku_demo.py` next to the running panel; the header comment
is a map of the layer stack.

## When it doesn't work

`./kit/verify.sh` checks these in order and tells you which one you are on.

| Symptom | Most likely cause |
|---|---|
| Backlight on, black screen | No DRM device — an fbtft/`/dev/fb1` panel, or the overlay isn't loaded ([DISPLAY.md](DISPLAY.md)) |
| Black screen, no errors in the log at all | `libdrm_display.so` didn't compile, so rendering went to a dummy buffer. `make -C drm_display` |
| Black screen, but Pi OS *with desktop* | A compositor holds the DRM master lock. `sudo systemctl set-default multi-user.target` |
| Screen works, taps do nothing | Not in the `input` group, or no touch overlay. `python -m drm_touch` |
| Taps land in the wrong place | Resistive panel needs calibrating / axes swapped — see `drm_touch` |
| Nothing on screen but *everything* passes | Backlight brightness pot turned to zero (`BRI-ADJ` on the board) |
| Random reboots under load | Under-powered supply. 5 V 2 A minimum |
| Feels slow | `--benchmark`, then [BOM.md § Performance](BOM.md#performance) |

## Making a golden image

For a class, a workshop, or a second kit, don't install twice.  Build one card,
verify it, shut down, then from a machine with the card in a reader:

```bash
sudo dd if=/dev/sdX of=sudoku-kit.img bs=4M status=progress   # check sdX twice
```

Shrink it with [PiShrink](https://github.com/Drewsif/PiShrink) so it fits any
card of the same nominal size, and flash copies with Imager.  Each copy needs
its own hostname and Wi-Fi if you want them on a network — Imager's settings
apply on top of a custom image.

This is also the honest answer to *"just plug the SD in and go"*: for one build,
the remote install is the flow; for ten, image the first one.
