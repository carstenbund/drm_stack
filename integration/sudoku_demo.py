#!/usr/bin/env python3
"""Sudoku — a real app on the stack, built out of layers.

The most involved demo here: a playable, uniquely-solvable sudoku with a digit
pad and live per-digit stats.  It exists to show what the layer model buys you
once an app has *state* — nothing here reaches for a widget toolkit, and after
the first frame no scene is ever recompiled wholesale.

    ┌──────────────────────────────┬──────────────┐   z    layer      repainted
    │  5 3 .  . 7 .  . . .         │   SUDOKU     │ ───────────────────────────
    │  6 . .  1 9 5  . . .         │  ┌──┬──┬──┐  │ 3000  scrim  ← on a win
    │  . 9 8  . . .  . 6 .         │  │ 1│ 2│ 3│  │ 1020  keys   digit finished
    │                              │  ├──┼──┼──┤  │ 1010  cells  changed cells
    │  8 . .  . 6 .  . . 3         │  │ 4│ 5│ 6│  │   25  hud    every event
    │  4 . .  8 . 3  . . 1         │  ├──┼──┼──┤  │   20  panel  on a count change
    │  7 . .  . 2 .  . . 6         │  │ 7│ 8│ 9│  │    5  sel    moved, not drawn
    │                              │  └──┴──┴──┘  │    0  board  once per puzzle
    │  . 6 .  . . .  2 8 .         │  ●●●●●○○○○   │
    │  . . .  4 1 9  . . 5         │              │  (1010/1020 are the buttons
    │  . . .  . 8 .  . 7 9         │ Erase  Hint  │   drm_composer lifts out of
    │                              │ New    Quit  │   their parent layer)
    └──────────────────────────────┴──────────────┘

Three things the demo is really about:

**Only the top layers are alive.**  The board — grid rules, block shading,
givens — is inert paint at z=0.  Every touchable thing is a `<button>`, and
drm_composer lifts each one into its own interactive layer 1000 above its
parent, so the 81 cells and the 9 digit keys float above a board that can never
be hit.  `hit_test()` walks top-down and stops at the first of them.

**Painting is proportional to what changed.**  Selecting a cell repaints no
cell at all: the highlight is one cell-sized layer and selection is a
`SetPosition`, exactly how the cursor overlay works.  Entering a digit
re-compiles only the cells whose look actually changed (the new digit, plus any
cell that gained or lost a conflict) — because each `<button>` is its own layer,
a scene fragment holding three buttons updates three layers and leaves the other
78 untouched.  Layers are also cut to the size of what they show: the blend runs
over every visible layer on every frame, so the panel, the counters and the
status line are compiled as their own small scenes rather than as three more
full-screen sheets (worth ~2x a frame here — see `_component`).

**z-order is the modal.**  Solve the board and a scrim layer lands at z=3000
with the board's own hit layers at 1010: taps on cells stop reaching the game
because something is *on top of them*, not because a flag was checked.

The game itself lives in `sudoku.py` — pure rules, no pixels — and this file is
pure presentation.  Same split as the stack it runs on.

    .venv/bin/python integration/sudoku_demo.py                    # real display + pointer
    .venv/bin/python integration/sudoku_demo.py --difficulty hard
    .venv/bin/python integration/sudoku_demo.py --selftest         # headless, scripted game
"""

import argparse
import os
import queue
import sys
import textwrap
import time
from functools import partial

from PIL import Image

from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import DeleteLayer, SetPosition, ShowLayer, HideLayer
from drm_composer import Compositor, parse_action, Dispatcher
from drm_touch import find_pointer_source, fan_out, TouchReader, TouchEvent

import sudoku
from sudoku import CELLS, DIFFICULTIES

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sudoku_frame.png")

# ── z-order.  drm_composer puts a <button> 1000 above its parent layer, so the
# gaps below are what keep the interactive layers stacked the way the game needs:
# pad keys (1020) over cell keys (1010), and the win scrim (3000) over both.
Z_BOARD, Z_SEL, Z_CELLS, Z_PANEL, Z_KEYS, Z_HUD = 0, 5, 10, 20, 20, 25
Z_WIN, Z_BANNER = 2000, 2100

# Layers the win overlay owns — dropped when a new puzzle starts.
WIN_LAYERS = ("win", "banner", "scrim", "cmd:dismiss")

# ── palette ───────────────────────────────────────────────────────────────────
C_BG        = "#0b0f18"
C_BOARD     = "#141c2c"
C_BLOCK     = "#1a2438"      # every other 3x3 block, so the boxes read at a glance
C_LINE      = "#2b3750"
C_LINE_FAT  = "#68809f"
C_GIVEN     = "#e8eef7"
C_ENTRY     = "#6fc3ff"
C_CONFLICT  = "#ff6b6b"
C_PANEL     = "#101827"
C_TITLE     = "#ffffff"
C_MUTED     = "#7c8ca0"
C_LABEL     = "#9ecbff"
C_KEY       = "#26436b"
C_KEY_DONE  = "#2f5d45"
C_PIP_ON    = "#6fc3ff"
C_PIP_OFF   = "#25304a"
C_SEL_FILL  = "#5694eb40"    # translucent — the board shows through the highlight
C_SEL_EDGE  = "#8cc4ffe0"
C_TRANSPARENT = "#00000000"
CONTROLS = (("erase", "Erase", "#4a5162"), ("hint", "Hint", "#7a6320"),
            ("new", "New", "#2a6cae"), ("quit", "Quit", "#a33a3a"))

# Smallest type the panel text will shrink to before the layout reflows instead.
_MIN_TEXT = 11


# ── markup helpers (screen-HTML is just text; these keep the scenes readable) ──

def _box(x, y, w, h, color):
    return f'<box x="{x}" y="{y}" w="{w}" h="{h}" color="{color}"/>'


def _text(x, y, size, color, s):
    return f'<text x="{x}" y="{y}" size="{size}" color="{color}">{s}</text>'


def _key(action, x, y, w, h, label, color, text_color=C_TITLE, size=28):
    """A <button action=...> — compiles to an interactive layer with hit_id cmd:<action>."""
    return (f'<button action="{action}" x="{x}" y="{y}" w="{w}" h="{h}" '
            f'color="{color}" text-color="{text_color}" size="{size}">{label}</button>')


# ── geometry ──────────────────────────────────────────────────────────────────

class Layout:
    """Every rectangle in the demo, derived from the runtime display size.

    The scenes below are written in pixels rather than the percentages
    `page_demo` uses — a sudoku grid has to land on exact cell boundaries — so
    resolution independence lives here instead: nothing downstream contains a
    constant that wasn't computed from (W, H).
    """

    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.m = m = max(6, H // 36)
        # The panel takes a slice of the width; the board takes the rest of it,
        # or the full height, whichever runs out first.  Then square it off to a
        # whole number of cells and centre it in the space left over.
        self.pw = max(180, min(int(W * 0.30), int(H * 0.62)))
        column = W - self.pw - 3 * m
        self.cell = max(12, min(H - 2 * m, column) // 9)
        self.side = self.cell * 9
        self.bx = m + (column - self.side) // 2
        self.by = (H - self.side) // 2
        self.px, self.py = W - m - self.pw, m
        self.ph = H - 2 * m
        self.gap = max(2, self.pw // 40)

    # ── board ─────────────────────────────────────────────────────────────────

    def cell_xy(self, i: int) -> tuple[int, int]:
        r, c = sudoku.rc(i)
        return self.bx + c * self.cell, self.by + r * self.cell

    # ── panel bands (fractions of the panel height, so they scale together) ───

    def band(self, top: float, height: float) -> tuple[int, int, int, int]:
        """A full-width strip of the panel, in screen coordinates."""
        return (self.px, self.py + int(self.ph * top),
                self.pw, int(self.ph * height))

    def key_tile(self, digit: int) -> tuple[int, int, int, int]:
        """The 3x3 pad tile for `digit` — the key sits on top, the pips below."""
        _, top, _, height = self.band(0.28, 0.42)
        k = digit - 1
        return (self.px + (k % 3) * (self.pw // 3),
                top + (k // 3) * (height // 3),
                self.pw // 3, height // 3)

    def control(self, k: int) -> tuple[int, int, int, int]:
        # A fifth of the panel for four controls: on a 3.5" panel that is the
        # difference between a 14px-tall button and a tappable 23px one.
        _, top, _, height = self.band(0.72, 0.21)
        w, h = self.pw // 2, height // 2
        return (self.px + (k % 2) * w + self.gap, top + (k // 2) * h + self.gap,
                w - 2 * self.gap, h - 2 * self.gap)

    # Rects for the layers that are compiled at their own size (see _component).

    def panel_rect(self) -> tuple[int, int, int, int]:
        return self.px, self.py, self.pw, self.ph

    def hud_rect(self) -> tuple[int, int, int, int]:
        return self.band(0.15, 0.14)

    def note_rect(self) -> tuple[int, int, int, int]:
        return self.band(0.945, 0.055)


class SudokuApp:
    """The host: owns the puzzle, decides what changed, and submits only that."""

    def __init__(self, service, difficulty: str = "medium", seed=None):
        self.service = service
        self.W, self.H = service.backend.width, service.backend.height
        self.lay = Layout(self.W, self.H)
        self.compositor = Compositor(target=None)     # we only use .compile()
        self.difficulty = difficulty if difficulty in DIFFICULTIES else "medium"
        self.seed = seed
        self.running = True

        self.puzzle: sudoku.Puzzle | None = None
        self.sel: int | None = None
        self.solved = False
        self.note = ""
        self.games = 0
        self.started = 0.0
        self._painted: dict[int, tuple[str, str]] = {}   # cell -> look on screen
        self._counts: dict[int, int] | None = None       # counts the panel shows
        self._clock = -1                                 # seconds the hud shows

        # Registration is the allowlist, and here it is also the *board*: exactly
        # 81 cell commands and 9 digit commands can fire.  A scene asking for
        # cmd:cell:9,9 — or anything else — reaches no handler and does nothing.
        self.dispatch = Dispatcher(logger=_Denials(self))
        for i in range(CELLS):
            r, c = sudoku.rc(i)
            self.dispatch.on_command(f"cell:{r},{c}", partial(self.select, i))
        for digit in range(1, 10):
            self.dispatch.on_command(f"place:{digit}", partial(self.place, digit))
        (self.dispatch
         .on_command("erase", self.erase)
         .on_command("hint", self.hint)
         .on_command("new", self.new_game)
         .on_command("dismiss", self.new_game)      # the win banner
         .on_action("scrim", self.new_game)         # anywhere else on the win scrim
         .on_action("quit", self._quit))

    # ── scenes ────────────────────────────────────────────────────────────────

    def _screen(self, body: str) -> str:
        return f'<screen width="{self.W}" height="{self.H}">{body}</screen>'

    def _board_scene(self) -> str:
        """Inert paint: background, block shading, grid rules.  Never hit-tested,
        compiled once per puzzle."""
        L = self.lay
        out = [_box(0, 0, self.W, self.H, C_BG),
               _box(L.bx, L.by, L.side, L.side, C_BOARD)]
        for b in range(9):
            if (b // 3 + b % 3) % 2:                       # checker the 3x3 boxes
                out.append(_box(L.bx + (b % 3) * 3 * L.cell,
                                L.by + (b // 3) * 3 * L.cell,
                                3 * L.cell, 3 * L.cell, C_BLOCK))
        thin = max(1, L.cell // 28)
        fat = max(2, L.cell // 12)
        for k in range(10):
            t, color = (fat, C_LINE_FAT) if k % 3 == 0 else (thin, C_LINE)
            off = k * L.cell - (t if k == 9 else 0)         # keep the last rule inside
            out.append(_box(L.bx + off, L.by, t, L.side, color))
            out.append(_box(L.bx, L.by + off, L.side, t, color))
        return self._screen(f'<layer id="board" z="{Z_BOARD}">{"".join(out)}</layer>')

    def _cells_scene(self, looks: dict[int, tuple[str, str]]) -> str:
        """The cell keys — one transparent <button> per cell, digit as its label.

        A cell is a hit target and a digit in one element: the button's rounded
        rect is fully transparent, so all that shows is the label, centred by the
        painter (no font metrics up here).  The parent layer carries no pixels of
        its own, hence visible="false" — only its z matters, as the base the
        buttons are lifted 1000 above.
        """
        L = self.lay
        size = int(L.cell * 0.62)
        keys = []
        for i, (label, color) in sorted(looks.items()):
            x, y = L.cell_xy(i)
            r, c = sudoku.rc(i)
            keys.append(_key(f"cell:{r},{c}", x, y, L.cell, L.cell, label,
                             C_TRANSPARENT, text_color=color, size=size))
        return self._screen(f'<layer id="cells" z="{Z_CELLS}" visible="false">'
                            f'{"".join(keys)}</layer>')

    def _component(self, name: str, z: int, rect, body_at) -> str:
        """A scene compiled at *component* size rather than screen size.

        `<screen>` sets the canvas the painter allocates, and the blend in
        drm_screen costs whatever a layer covers — so a panel declared 1024x600
        wide pays a full-screen blend every frame to show 300 columns of pixels.
        Declaring the scene at the component's own size and placing the layer
        (see `_place`) is what keeps the frame affordable: on a 1024x600 panel
        that is three full-screen blends per frame instead of one.

        The catch, and the reason the pad keys live in `_keys_scene` instead:
        `<button>`s become layers of their *own*, positioned in scene
        coordinates, so a component-sized scene can only hold component-sized
        paint.  Paint here, buttons there.

        `body_at` is handed the rect and returns markup in the layer's own
        coordinates; `_place` then puts the layer where the rect said.
        """
        x, y, w, h = rect
        return (f'<screen width="{w}" height="{h}">'
                f'<layer id="{name}" z="{z}">{body_at(x, y, w, h)}</layer></screen>')

    @staticmethod
    def _place(name: str, batch: list, rect) -> list:
        """Move a component layer from the scene origin to its place on screen."""
        return batch + [SetPosition(name, rect[0], rect[1])]

    def _panel_scene(self, counts: dict[int, int]) -> str:
        """The pad's paint: chrome, the title, and nine placed/9 pip meters."""
        L = self.lay
        pad = L.gap * 2

        def body(ox, oy, w, h):
            # component-local coordinates: screen x minus the layer origin
            def box(x, y, bw, bh, color):
                return _box(x - ox, y - oy, bw, bh, color)

            def txt(x, y, size, color, s):
                return _text(x - ox, y - oy, size, color, s)

            out = [box(L.px, L.py, L.pw, L.ph, C_PANEL)]
            _, ty, _, th = L.band(0.0, 0.07)
            out.append(txt(L.px + pad, ty, int(th * 0.9), C_TITLE, "SUDOKU"))
            _, sy, _, sh = L.band(0.09, 0.05)
            out.append(txt(L.px + pad, sy, int(sh * 0.62), C_MUTED,
                           f"{self.difficulty} &#183; {self.puzzle.clues} givens"))
            for digit in range(1, 10):
                x, y, tw, tile_h = L.key_tile(digit)
                placed = counts[digit]
                # nine pips under the key: filled = on the board, hollow = to go
                pip = max(2, (tw - 2 * L.gap - 16) // 9)
                step = pip + 2
                px0 = x + (tw - (9 * step - 2)) // 2
                py0 = y + int(tile_h * 0.74)
                for k in range(9):
                    out.append(box(px0 + k * step, py0, pip, pip,
                                   C_PIP_ON if k < placed else C_PIP_OFF))
            return "".join(out)

        return self._component("panel", Z_PANEL, self.lay.panel_rect(), body)

    def _keys_scene(self, counts: dict[int, int]) -> str:
        """The pad's keys — nine digits and the four controls.

        Full-screen scene, but the parent layer is `visible="false"` and carries
        no paint: it exists only as the z the keys are lifted 1000 above.  A
        hidden layer is skipped by both the blend and the hit test, so the
        wrapper is free and only the keys themselves cost anything.
        """
        L = self.lay
        out = []
        for digit in range(1, 10):
            x, y, w, h = L.key_tile(digit)
            done = counts[digit] >= 9
            key_h = int(h * 0.66) - L.gap
            out.append(_key(f"place:{digit}", x + L.gap, y + L.gap,
                            w - 2 * L.gap, key_h, str(digit),
                            C_KEY_DONE if done else C_KEY,
                            text_color=C_MUTED if done else C_TITLE,
                            size=int(key_h * 0.62)))
        for k, (action, label, color) in enumerate(CONTROLS):
            x, y, w, h = L.control(k)
            size = max(_MIN_TEXT, int(h * 0.45))
            # Quit keeps the bare-id form the other demos use (hit_id "quit");
            # the rest go through cmd: so they sit behind the command allowlist.
            if action == "quit":
                out.append(f'<button id="quit" x="{x}" y="{y}" w="{w}" h="{h}" '
                           f'color="{color}" size="{size}">{label}</button>')
            else:
                out.append(_key(action, x, y, w, h, label, color, size=size))
        return self._screen(f'<layer id="keys" z="{Z_KEYS}" visible="false">'
                            f'{"".join(out)}</layer>')

    def _hud_scene(self) -> str:
        """Live counters — a strip of the panel, so a tick of the clock
        re-blends a few thousand pixels rather than the whole screen.

        Reflows to fit: one counter per line where there is room, two columns of
        two (with shortened labels) where there is not.  A 480x320 3.5" panel
        gives this strip about 40 pixels of height, which is the case that
        decides the layout.
        """
        L = self.lay
        conflicts = len(self.puzzle.conflicts())
        filled = f"{self.puzzle.filled()} / {CELLS}"
        rows = [("filled", "fill", filled, filled.replace(" ", ""), C_LABEL),
                ("conflicts", "clash", str(conflicts), str(conflicts),
                 C_CONFLICT if conflicts else C_LABEL),
                ("moves", "move", str(self.puzzle.moves), str(self.puzzle.moves),
                 C_LABEL),
                ("time", "time", _mmss(self.elapsed()), _mmss(self.elapsed()),
                 C_LABEL)]

        def body(ox, oy, w, h):
            cols = 1 if int(h / (len(rows) * 1.35)) >= _MIN_TEXT else 2
            per_col = -(-len(rows) // cols)
            col_w, narrow = w // cols, cols > 1
            pad = L.gap * 2
            labels = [r[1] if narrow else r[0] for r in rows]
            values = [r[3] if narrow else r[2] for r in rows]
            # Fit vertically, and keep the widest label+value pair inside a column.
            room = max(len(a) + len(b) + 1 for a, b in zip(labels, values))
            size = max(_MIN_TEXT - 2, min(int(h / (per_col * 1.35)),
                                          int((col_w - 2 * pad) / (room * 0.58))))
            out = []
            for k, (label, value) in enumerate(zip(labels, values)):
                cx = (k // per_col) * col_w
                ry = (k % per_col) * int(size * 1.35)
                out.append(_text(cx + pad, ry, size, C_MUTED, label))
                # values are right-aligned, so they line up and can never run
                # into the next column (0.62em is a safe over-estimate for digits)
                vw = int(len(value) * size * 0.62)
                out.append(_text(cx + col_w - pad - vw, ry, size, rows[k][4], value))
            return "".join(out)

        return self._component("hud", Z_HUD, self.lay.hud_rect(), body)

    def _note_scene(self) -> str:
        """The status line, pinned to the foot of the panel."""
        L = self.lay

        def body(ox, oy, w, h):
            size = max(11, int(h * 0.38))
            return "".join(
                _text(L.gap * 2, k * int(size * 1.25), size, C_MUTED, line)
                for k, line in enumerate(_wrap(self.note, w - L.gap * 4, size)))

        return self._component("note", Z_HUD, self.lay.note_rect(), body)

    def _sel_scene(self) -> str:
        """The selection frame: a translucent wash inside a bright border.

        A *cell-sized* component, which is the whole point — a highlight this
        small can be moved with `SetPosition` instead of repainted, the same
        shape as drm_screen's cursor overlay.  Within one layer the painter
        replaces rather than blends, so the four edge boxes sit cleanly on the
        wash; the wash itself is translucent, so the grid shows through when
        drm_screen composites the layer over the board.
        """
        cell = self.lay.cell
        t = max(2, cell // 20)

        def body(ox, oy, w, h):
            return "".join([_box(0, 0, w, h, C_SEL_FILL),
                            _box(0, 0, w, t, C_SEL_EDGE),
                            _box(0, h - t, w, t, C_SEL_EDGE),
                            _box(0, 0, t, h, C_SEL_EDGE),
                            _box(w - t, 0, t, h, C_SEL_EDGE)])

        return self._component("sel", Z_SEL, (0, 0, cell, cell), body)

    def _win_scene(self) -> str:
        """The modal, expressed purely as z-order.

        `scrim` is a transparent button covering the whole board: at z=3000 it
        outranks the cell keys at 1010, so hit_test() returns it and the cells
        below stop existing as far as input is concerned.  No flag, no capture.
        """
        L = self.lay
        msg = f"Solved in {self.puzzle.moves} moves &#8212; tap for a new puzzle"
        bw, bh = int(L.side * 0.86), max(40, int(L.cell * 1.1))
        bx = L.bx + (L.side - bw) // 2
        by = L.by + (L.side - bh) // 2
        # size the banner text so it cannot overflow its own button
        size = int(min(L.cell * 0.42, bw * 0.92 / (0.56 * len(msg))))
        return self._screen(
            f'<layer id="win" z="{Z_WIN}">'
            f'{_box(L.bx, L.by, L.side, L.side, "#0a0f1cc0")}'
            f'<button id="scrim" x="{L.bx}" y="{L.by}" w="{L.side}" h="{L.side}" '
            f'color="{C_TRANSPARENT}"/>'
            f'</layer>'
            f'<layer id="banner" z="{Z_BANNER}" visible="false">'
            f'{_key("dismiss", bx, by, bw, bh, msg, "#2f7d52", size=max(11, size))}'
            f'</layer>')

    # ── batches: each returns commands for exactly what changed ───────────────

    def _sel_batch(self) -> list:
        """Create the selection layer, hidden until a cell is picked."""
        return self.compositor.compile(self._sel_scene()) + [HideLayer("sel")]

    def _cell_look(self, i: int, conflicts: set[int]) -> tuple[str, str]:
        v = self.puzzle.value(i)
        if not v:
            return ("", C_MUTED)
        if i in conflicts:
            return (str(v), C_CONFLICT)
        return (str(v), C_GIVEN if self.puzzle.is_given(i) else C_ENTRY)

    def _cells_batch(self, force: bool = False) -> list:
        """Re-compile only the cells whose look differs from what is on screen."""
        conflicts = self.puzzle.conflicts()
        looks = {i: self._cell_look(i, conflicts) for i in range(CELLS)}
        changed = ({i: look for i, look in looks.items()
                    if self._painted.get(i) != look} if not force else looks)
        if not changed:
            return []
        self._painted = looks
        return self.compositor.compile(self._cells_scene(changed))

    def _panel_batch(self, force: bool = False) -> list:
        """Repaint the pip meters when a digit count moves; re-cut the keys only
        when one of them finishes (nine on the board) or comes back into play."""
        counts = self.puzzle.counts()
        if counts == self._counts and not force:
            return []
        was_done = {d for d, n in (self._counts or {}).items() if n >= 9}
        is_done = {d for d, n in counts.items() if n >= 9}
        self._counts = counts
        batch = self._place("panel",
                            self.compositor.compile(self._panel_scene(counts)),
                            self.lay.panel_rect())
        if force or was_done != is_done:
            batch += self.compositor.compile(self._keys_scene(counts))
        return batch

    def _hud_batch(self) -> list:
        self._clock = int(self.elapsed())
        return (self._place("hud", self.compositor.compile(self._hud_scene()),
                            self.lay.hud_rect())
                + self._place("note", self.compositor.compile(self._note_scene()),
                              self.lay.note_rect()))

    # ── game flow ─────────────────────────────────────────────────────────────

    def new_game(self) -> None:
        seed = None if self.seed is None else self.seed + self.games
        self.games += 1
        self.puzzle = sudoku.generate(clues=DIFFICULTIES[self.difficulty], seed=seed)
        self.sel = None
        self.solved = False
        self.note = "tap a cell, then a digit"
        self.started = time.monotonic()
        self._painted, self._counts = {}, None
        self.service.submit(
            [DeleteLayer(name) for name in WIN_LAYERS]
            + self.compositor.compile(self._board_scene())
            + self._sel_batch()
            + self._cells_batch(force=True)
            + self._panel_batch(force=True)
            + self._hud_batch())

    def select(self, i: int) -> None:
        """Pick a cell: one layer move and a status line — no cell is repainted."""
        self.sel = i
        x, y = self.lay.cell_xy(i)
        self.note = ("that one is a given" if self.puzzle.is_given(i)
                     else "now tap a digit")
        self.service.submit([SetPosition("sel", x, y), ShowLayer("sel")]
                            + self._hud_batch())

    def place(self, digit: int) -> None:
        if self.sel is None:
            return self._note("pick a cell first")
        if self.puzzle.is_given(self.sel):
            return self._note("that one is a given")
        # tapping the digit already in the cell takes it back out
        self.puzzle.set(self.sel, 0 if self.puzzle.entries[self.sel] == digit else digit)
        self.note = "" if self.puzzle.conflicts() == set() else "that digit clashes"
        self._commit()

    def erase(self) -> None:
        if self.sel is None or self.puzzle.is_given(self.sel):
            return self._note("pick one of your digits")
        self.puzzle.clear(self.sel)
        self.note = ""
        self._commit()

    def hint(self) -> None:
        """Reveal one cell that isn't right yet — the selected one for choice.

        With nothing selected it walks the board, so holding Hint down solves the
        puzzle a cell at a time (handy for showing off the win overlay).
        """
        def wrong(k):
            return (not self.puzzle.is_given(k)
                    and self.puzzle.entries[k] != self.puzzle.solution[k])

        i = (self.sel if self.sel is not None and wrong(self.sel)
             else next((k for k in range(CELLS) if wrong(k)), None))
        if i is None:
            return self._note("nothing left to reveal")
        self.puzzle.hint(i)
        self.sel = i
        x, y = self.lay.cell_xy(i)
        self.note = "revealed"
        self._commit([SetPosition("sel", x, y), ShowLayer("sel")])

    def _commit(self, extra: list | None = None) -> None:
        """Push a move: changed cells, the pad if a count moved, the hud, the win."""
        batch = list(extra or []) + self._cells_batch() + self._panel_batch()
        if self.puzzle.is_solved():
            self.solved = True
            self.note = "solved!"
            batch += [HideLayer("sel")] + self.compositor.compile(self._win_scene())
        self.service.submit(batch + self._hud_batch())

    def _note(self, msg: str) -> None:
        self.note = msg
        self.service.submit(self._hud_batch())

    def _quit(self) -> None:
        self.running = False

    # ── clock ─────────────────────────────────────────────────────────────────

    def elapsed(self) -> float:
        return 0.0 if not self.started else time.monotonic() - self.started

    def tick(self) -> None:
        """Called from the idle path of the input loop: refresh the clock ~1/s."""
        if self.solved or int(self.elapsed()) == self._clock:
            return
        self.service.submit(self._hud_batch())

    # ── input ─────────────────────────────────────────────────────────────────

    def on_event(self, ev) -> None:
        if ev.phase != "down":
            return
        hit = self.service.hit_test(ev.x, ev.y)
        if hit is None:            # empty space — nothing is claiming that pixel
            return
        self.dispatch.dispatch(parse_action(hit))

    def button_center(self, hit_id: str) -> tuple[int, int] | None:
        for layer in self.service.composer.layers.values():
            if layer.interactive and layer.hit_id == hit_id:
                return layer.x + layer.width // 2, layer.y + layer.height // 2
        return None


class _Denials:
    """Logger the Dispatcher calls for a hit nothing was registered for."""

    def __init__(self, app):
        self.app = app

    def info(self, fmt, *args):
        self.app.note = "ignored: " + (str(args[0]) if args else fmt)


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _wrap(msg: str, width_px: int, size: int) -> list[str]:
    """Break a status line to the panel width (a rough advance of 0.52em/char)."""
    if not msg:
        return []
    return textwrap.wrap(msg, max(8, int(width_px / (size * 0.52))))[:2]


# ── runners ───────────────────────────────────────────────────────────────────

def run_real(args):
    backend = DrmDisplayBackend(device=args.device, width=args.width, height=args.height)
    service = ScreenService(backend, fps=60)
    app = SudokuApp(service, difficulty=args.difficulty, seed=args.seed)
    service.start()
    app.new_game()

    app_q: queue.Queue = queue.Queue()
    source = find_pointer_source(app.W, app.H, prefer=args.source)
    print(f"input source: {type(source).__name__}")
    reader = TouchReader(source, fan_out(service.submit, app_q))
    reader.start()
    try:
        while app.running:
            try:
                ev = app_q.get(timeout=0.1)
            except queue.Empty:
                app.tick()               # the clock, independent of any input
                continue
            app.on_event(ev)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        backend.screen.clear()
        service.stop()
    return 0


def run_selftest():
    """Play a seeded puzzle through hit_test — no display, no input hardware."""
    backend = DrmDisplayBackend(device="dummy", width=1024, height=600)
    service = ScreenService(backend)
    app = SudokuApp(service, difficulty="easy", seed=11)
    app.new_game()
    service.render_once()

    app_q: queue.Queue = queue.Queue()
    sink = fan_out(service.submit, app_q)

    def tap(hit_id):
        c = app.button_center(hit_id)
        assert c is not None, f"{hit_id} is not on screen"
        return tap_at(*c)

    def tap_at(x, y):
        sink(TouchEvent("down", x, y))
        while not app_q.empty():
            app.on_event(app_q.get_nowait())
        service.render_once()
        return service.hit_test(x, y)

    def cell(i):
        r, c = sudoku.rc(i)
        return f"cmd:cell:{r},{c}"

    p = app.puzzle
    # two empty cells sharing a row — the pair the conflict check needs
    base, twin = next((a, b) for r in range(9)
                      for a in range(r * 9, r * 9 + 9) if not p.givens[a]
                      for b in range(a + 1, r * 9 + 9) if not p.givens[b])

    # the board is paint; only the cell keys and the pad are hit-testable
    board = service.composer.layers["board"]
    assert not board.interactive and board.z == Z_BOARD
    keys = [l for l in service.composer.layers.values() if l.interactive]
    assert len(keys) == CELLS + len(CONTROLS) + 9, len(keys)
    assert min(l.z for l in keys) > board.z

    # selecting a cell moves one layer and repaints nothing
    painted = dict(app._painted)
    tap(cell(base))
    assert app.sel == base and service.composer.layers["sel"].visible
    sel = service.composer.layers["sel"]
    assert (sel.x, sel.y) == app.lay.cell_xy(base), "highlight is off the cell"
    assert app._painted == painted, "selection repainted a cell"

    # place the right digit, then a clashing one, then take it back out
    tap("cmd:place:%d" % p.solution[base])
    assert p.entries[base] == p.solution[base]
    assert not p.conflicts(), "a correct digit should not clash"

    tap(cell(twin))
    tap("cmd:place:%d" % p.entries[base])                # same digit, same row
    assert p.conflicts() >= {twin, base}, "duplicate in a row went unnoticed"
    assert app._painted[twin][1] == C_CONFLICT, "clash not shown in red"
    assert app._painted[base][1] == C_CONFLICT, "the other end of the clash is unmarked"
    Image.fromarray(backend.snapshot_rgba(), "RGBA").save(OUT)
    tap("cmd:erase")
    assert not p.conflicts() and p.entries[twin] == 0

    # per-digit stats track the board
    digit = p.solution[base]
    assert app._counts[digit] == p.counts()[digit]

    # the allowlist: registration covers exactly the 81 real cells
    assert app.dispatch.dispatch(parse_action("cmd:cell:9,9")) is False
    assert app.dispatch.dispatch(parse_action("cmd:place:0")) is False
    assert app.dispatch.dispatch(parse_action("cmd:rm-rf")) is False

    # solve it: Hint with nothing selected fills the first empty cell
    for _ in range(CELLS):
        if p.is_solved():
            break
        tap("cmd:hint")
    assert p.is_solved(), f"{p.filled()}/{CELLS} filled"
    assert app.solved and not service.composer.layers["sel"].visible

    # the win scrim outranks the cell keys, so board taps stop reaching the game
    cx, cy = app.button_center(cell(base))
    moves_before = p.moves
    assert service.hit_test(cx, cy) == "scrim", service.hit_test(cx, cy)

    # tapping it starts a fresh puzzle (and the scrim is gone again)
    tap_at(cx, cy)
    assert app.puzzle is not p and not app.solved
    assert "scrim" not in service.composer.layers
    assert app.puzzle.moves == 0 and moves_before > 0

    tap("quit")
    assert app.running is False
    print(f"selftest OK — solved a seeded board through hit_test -> {OUT}")
    service.stop()
    return 0


def run_benchmark(args):
    """Time the work a move costs, on the dummy backend — no display needed.

    Runs entirely on the CPU path (compile + composite), so it is safe over SSH
    and measures the thing that actually decides whether a small board feels
    responsive.  `drm_screen` re-blends every visible layer per frame, so the
    number scales with pixel count: check it at your panel's real resolution.
    """
    W = args.width or 480
    H = args.height or 320
    backend = DrmDisplayBackend(device="dummy", width=W, height=H)
    service = ScreenService(backend)
    app = SudokuApp(service, difficulty=args.difficulty, seed=args.seed or 1)

    t0 = time.monotonic()
    app.new_game()
    service.render_once()
    boot = (time.monotonic() - t0) * 1000

    empty = [i for i in range(CELLS) if not app.puzzle.givens[i]]
    moves, compile_ms, render_ms = 0, 0.0, 0.0
    for i in empty[:args.benchmark]:
        app.select(i)
        service.render_once()
        t = time.monotonic()
        app.place(app.puzzle.solution[i])          # compile: diff + scene -> commands
        compile_ms += (time.monotonic() - t) * 1000
        t = time.monotonic()
        service.render_once()                      # composite: layers -> frame
        render_ms += (time.monotonic() - t) * 1000
        moves += 1
    service.stop()

    per = (compile_ms + render_ms) / max(1, moves)
    print(f"sudoku benchmark @ {W}x{H}  ({moves} moves)")
    print(f"  first frame     {boot:7.1f} ms   (board + 81 cell keys + pad)")
    print(f"  compile / move  {compile_ms / max(1, moves):7.1f} ms   (scene -> commands)")
    print(f"  composite/move  {render_ms / max(1, moves):7.1f} ms   (layers -> frame)")
    print(f"  total    / move {per:7.1f} ms   -> {1000 / per:.1f} moves/s")
    if per > 400:
        print("  verdict: sluggish but playable — try a smaller panel, or a Pi Zero 2 W")
    elif per > 150:
        print("  verdict: fine for a turn-based game (a tap costs one frame)")
    else:
        print("  verdict: snappy")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None, help="display backend (default: auto)")
    ap.add_argument("--width", type=int, default=None, help="force display width")
    ap.add_argument("--height", type=int, default=None, help="force display height")
    ap.add_argument("--difficulty", default="medium", choices=sorted(DIFFICULTIES),
                    help="how many givens the puzzle keeps (default: medium)")
    ap.add_argument("--seed", type=int, default=None,
                    help="reproducible puzzles (default: random)")
    ap.add_argument("--source", default=None,
                    choices=["touch", "composite", "abs", "mouse", "dummy"])
    ap.add_argument("--selftest", action="store_true", help="headless scripted game")
    ap.add_argument("--benchmark", type=int, nargs="?", const=12, metavar="MOVES",
                    help="time a move at --width/--height (default 480x320); "
                         "headless, safe over SSH")
    args = ap.parse_args()
    if args.selftest:
        return run_selftest()
    if args.benchmark:
        return run_benchmark(args)
    return run_real(args)


if __name__ == "__main__":
    sys.exit(main())
