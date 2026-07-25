# Assembly — soldering the header and mounting the panel

Two steps and a check.  Budget 30 minutes for your first one, 10 for the next.
If you bought a **Pi Zero WH** (header pre-fitted), skip to step 2.

> **Before you start:** nothing is powered.  No SD card, no USB, no display.
> Solder first, inspect, *then* power up once — a bridged joint found with a
> multimeter costs nothing; found by a power supply it can cost the board.

---

## Step 1 — the 2×20 header on the Pi Zero

The Pi Zero ships with 40 bare holes.  The display board has a matching female
socket, so the Zero needs male pins pointing **up** — the same side as the
processor and the ports, so the panel sits on top of the Pi.

### Set up

- Iron at **320–350 °C** with a fine conical or small chisel tip.  A fixed 25 W
  pencil works; it just gives you less margin.
- Tip clean and *tinned* — a dull grey tip transfers almost no heat, which is the
  usual reason a first header goes badly.
- Work somewhere ventilated.  Rosin flux smoke is an irritant; don't breathe it.
- Ground yourself if you can (touch a radiator or a metal case first).  Pi Zeros
  are not delicate, but static is free to avoid.

### Get it square before you solder 38 joints

1. Push the header through from the top so the **short** pins stick out
   underneath and the long pins point up. Plastic spacer flush against the board.
2. Hold it in place — a helping-hands clamp, or rest the Pi upside-down on the
   header on a flat surface so gravity keeps it seated.
3. **Solder pin 1 only.**  Pin 1 is the corner nearest the microSD end, on the
   inner row; it is the one with a square pad on most boards.
4. **Solder pin 40** — the diagonally opposite corner.
5. Now look at it from the side, at eye level.  Is the header perpendicular to
   the board?  Is the spacer flush all the way along?  If it leans, re-melt one
   of the two corner joints and nudge it straight.  **This is the only moment
   fixing it is easy.** Once 40 pins are down, straightening it means desoldering
   40 pins, which usually ends in lifted pads.

### The other 38

Then work along the rows in order, one pin at a time:

1. Touch the tip to the **pad and the pin at once**, in the crook where they meet.
2. Feed solder in for **half a second**, from the opposite side of the joint.
3. Pull the solder away, then the iron — in that order.
4. Move on.  **1–2 seconds per joint, no more.**  Dwelling is what lifts pads.

A good joint is a small shiny cone that wets both the pin and the whole pad.  A
ball sitting on top of the pin, or a dull crusty blob, is a cold joint — re-flow
it with a touch of fresh solder (fresh solder carries fresh flux, which is what
actually fixes it).

Working down a row heats one area steadily; if the board starts to smell hot or
the plastic spacer softens, stop for a minute.  Nothing is on a timer.

### Common ways this goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Blob bridging two pins | Too much solder, or tip too cool | Add flux, drag the tip across the bridge toward the outside edge; or wick it with desoldering braid |
| Dull grey lumpy joint | Cold — joint moved while solidifying, or tip not hot enough | Re-flow with fresh solder |
| Solder won't stick to a pad | Oxidised pad or no flux | Flux, then a *quick* touch — don't cook it |
| Pad lifted / ring of copper gone | Iron dwelled too long | Recoverable only with a wire jumper to the next point on the net. Best avoided |
| Header leans visibly | Corners soldered out of square | Re-melt a corner joint early; late, live with it if it still mates |

### Check before power (5 minutes, do not skip)

Multimeter on continuity/beep:

- **Pin 1 (3V3) to pin 2 (5V)** — must be **open**.  A beep here is a dead short
  between rails; find and clear the bridge before you plug anything in.
- **Pin 2 (5V) to pin 6 (GND)** — must be open.
- **Pin 1 (3V3) to pin 6 (GND)** — must be open.
- Pin 6 to pin 9 to pin 14 (all GND) — should beep (they are the same net).
- Each pin to its own pad on the underside — should beep.  This catches the cold
  joint that looks fine and conducts nothing.

Then hold the board to a light and sight down each row: 40 similar cones, no
bridges, no missed pins.

Clean the flux off with isopropyl and a brush if you used anything other than
no-clean solder.  Cosmetic, but it makes inspection honest.

---

## Step 2 — mounting the panel

1. **Match the connector, don't force it.**  Most 3.5" boards mate through the
   socket you just made a header for.  Line up pin 1 to pin 1 — with a
   40-pin socket it is possible to sit it one row over, which puts 5 V somewhere
   it does not belong.  Count the overhang at both ends before pushing down.
2. **Press evenly**, thumbs over the socket, not on the panel glass.
3. **Standoffs at the corners.**  Fit them before you rely on the header
   mechanically — 40 pins will hold the boards together, but flexing them is what
   cracks joints later.  Snug, not tight; M2.5 threads in nylon strip easily.
4. **If your board has a ribbon (FFC/FPC) to the panel** — like the brown ribbon
   in the photo — it is normally fitted at the factory and best left alone.  If
   you must reseat it: flip the black latch *up* (it hinges, it does not pull
   out), slide the ribbon in square and fully home, contacts facing the way they
   came out, then close the latch. Never force a closed latch.
5. **The brightness pot** (`BRI-ADJ` on the board in the photo) sets the
   backlight.  Turn it with a *plastic* trimmer tool, quarter-turn at a time, and
   don't drive it past its end stops.  If your first boot shows a black screen,
   this is the very first thing to check — a panel at zero brightness and a panel
   with no driver look identical.

---

## Step 3 — first power-up

In this order:

1. Card out, display attached, nothing else plugged in.
2. Power into the port marked **PWR IN** (the Zero has two micro-USB ports; the
   other one is the data/OTG port and will not power the board properly).
3. Watch for 10 seconds: no smell, nothing hot to a fingertip, the green ACT LED
   blinking. A steady-on or dark ACT LED with no card is normal.
4. Pull the power, insert the flashed card, power up again.

Backlight on but no image is expected at this stage — the display driver is not
configured yet.  That is [DISPLAY.md](DISPLAY.md) and `install.sh`.

If anything gets hot enough to be uncomfortable, unplug immediately and go back
to the continuity checks.
