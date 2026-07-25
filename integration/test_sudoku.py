"""The sudoku demo — the game's rules, and the layer contract it leans on.

Two halves.  The first pins `sudoku.py`, which is pure logic and needs no
display at all.  The second is what this repo exists for: the app is driven
exclusively through `hit_test()`, so these check the boundaries no single
package can — a `<button>` becoming an interactive layer at the right z, a
digit reaching real pixels, and input stopping at whatever is on top.

Headless throughout; no display or input hardware.
"""

import numpy as np
import pytest

from drm_composer import parse_action, parse_scene, paint_scene
from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import CreateLayer, PlaceRawBuffer, SetPosition
from drm_touch import TouchEvent

import sudoku
import sudoku_demo
from sudoku import CELLS
from sudoku_demo import SudokuApp

W, H = 1024, 600


# ── the model ─────────────────────────────────────────────────────────────────

def test_generated_puzzle_is_uniquely_solvable():
    p = sudoku.generate(clues=32, seed=1)
    assert sudoku.is_unique(p.givens)
    # the givens are the solution's, just with holes in it
    assert all(g in (0, s) for g, s in zip(p.givens, p.solution))
    assert not sudoku.Puzzle(p.solution, p.solution).conflicts()


def test_generation_is_reproducible_and_seed_dependent():
    assert sudoku.generate(clues=32, seed=5).givens == \
           sudoku.generate(clues=32, seed=5).givens
    assert sudoku.generate(clues=32, seed=5).givens != \
           sudoku.generate(clues=32, seed=6).givens


def test_givens_are_mirror_symmetric():
    # cells are dug in pairs (i, 80-i), so the finished pattern is 180-symmetric
    g = sudoku.generate(clues=32, seed=2).givens
    assert all(bool(g[i]) == bool(g[CELLS - 1 - i]) for i in range(CELLS))


@pytest.mark.parametrize("a,b", [(0, 1), (0, 9), (0, 10)])   # row, column, box
def test_conflicts_mark_both_ends_of_a_clash(a, b):
    p = sudoku.Puzzle([0] * CELLS, [0] * CELLS)
    p.set(a, 4)
    assert not p.conflicts()
    p.set(b, 4)
    assert p.conflicts() == {a, b}


def test_conflicts_include_a_given_the_player_clashed_with():
    givens = [0] * CELLS
    givens[0] = 7
    p = sudoku.Puzzle(givens, [0] * CELLS)
    p.set(8, 7)                                  # same row as the given
    assert p.conflicts() == {0, 8}


def test_givens_are_not_writable():
    p = sudoku.generate(clues=45, seed=3)
    given = next(i for i in range(CELLS) if p.givens[i])
    assert p.set(given, 1) is False
    assert p.value(given) == p.givens[given] and p.moves == 0


def test_counts_and_solved_state():
    p = sudoku.generate(clues=45, seed=4)
    assert sum(p.counts().values()) == p.filled() == p.clues
    assert not p.is_solved()
    for i in range(CELLS):
        p.hint(i)
    assert p.is_solved() and p.grid() == p.solution
    assert p.counts() == {d: 9 for d in range(1, 10)}


def test_solver_counts_solutions():
    p = sudoku.generate(clues=32, seed=8)
    assert sudoku.solve(p.givens) == p.solution
    assert len(sudoku.solutions([0] * CELLS, limit=3)) == 3   # empty grid: many
    # a grid that already contradicts itself has no solution, full or not
    broken = list(p.solution)
    broken[1] = broken[0]                                     # duplicate in row 0
    assert sudoku.solve(broken) is None
    broken[40] = 0
    assert sudoku.solve(broken) is None


# ── the app on the stack ──────────────────────────────────────────────────────

def _app(difficulty="easy", seed=11):
    service = ScreenService(DrmDisplayBackend(device="dummy", width=W, height=H))
    app = SudokuApp(service, difficulty=difficulty, seed=seed)
    app.new_game()
    service.render_once()
    return app


def _tap(app, x, y):
    app.on_event(TouchEvent("down", x, y))
    app.service.render_once()


def _tap_id(app, hit_id):
    c = app.button_center(hit_id)
    assert c is not None, f"{hit_id} is not on screen"
    _tap(app, *c)


def _record(app):
    """Capture the command batches the app submits from here on."""
    batches, original = [], app.service.submit

    def spy(commands):
        commands = list(commands)
        batches.append(commands)
        original(commands)

    app.service.submit = spy
    return batches


def _empty_cell(app):
    return next(i for i in range(CELLS) if not app.puzzle.givens[i])


def test_only_the_top_layers_are_interactive():
    app = _app()
    layers = app.service.composer.layers
    live = [l for l in layers.values() if l.interactive]
    # 81 cells + 9 digits + Erase/Hint/New/Quit, and nothing else
    assert len(live) == CELLS + 9 + 4
    assert {l.hit_id for l in live} >= {"cmd:cell:0,0", "cmd:place:1", "quit"}
    # the painted layers carry no input at all, and every live layer is above them
    for name in ("board", "panel", "hud", "note"):
        assert not layers[name].interactive
    assert min(l.z for l in live) > max(layers[n].z for n in ("board", "panel", "hud"))


def test_the_board_is_paved_with_cell_keys():
    app = _app()
    L = app.lay
    for i in (0, 40, 80):
        r, c = sudoku.rc(i)
        x, y = L.cell_xy(i)
        assert app.service.hit_test(x + 1, y + 1) == f"cmd:cell:{r},{c}"
        assert app.service.hit_test(x + L.cell - 1, y + L.cell - 1) == \
            f"cmd:cell:{r},{c}"
    # just outside the grid nothing claims the pixel
    assert app.service.hit_test(L.bx - 2, L.by - 2) is None


def test_an_empty_cell_key_is_a_hit_target_with_no_pixels():
    app = _app()
    r, c = sudoku.rc(_empty_cell(app))
    layer = app.service.composer.layers[f"cmd:cell:{r},{c}"]
    assert layer.interactive
    assert layer.buffer[..., 3].max() == 0, "an empty cell should paint nothing"


def test_selecting_a_cell_moves_one_layer_and_repaints_no_cells():
    app = _app()
    i = _empty_cell(app)
    batches = _record(app)
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(i))

    assert app.sel == i
    moves = [c for b in batches for c in b if isinstance(c, SetPosition)]
    assert SetPosition("sel", *app.lay.cell_xy(i)) in moves
    touched = {c.name for b in batches for c in b if isinstance(c, CreateLayer)}
    assert not any(n.startswith("cmd:cell:") for n in touched), touched
    assert app.service.composer.layers["sel"].visible


def test_a_move_recompiles_only_the_cells_that_changed():
    app = _app()
    i = _empty_cell(app)
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(i))
    batches = _record(app)
    _tap_id(app, "cmd:place:%d" % app.puzzle.solution[i])

    cells = {n for b in batches for c in b if isinstance(c, CreateLayer)
             for n in [c.name] if n.startswith("cmd:cell:")}
    assert cells == {"cmd:cell:%d,%d" % sudoku.rc(i)}, cells


def test_a_clash_repaints_both_cells_and_clears_together():
    app = _app()
    p = app.puzzle
    base, twin = next((a, b) for r in range(9)
                      for a in range(r * 9, r * 9 + 9) if not p.givens[a]
                      for b in range(a + 1, r * 9 + 9) if not p.givens[b])
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(base))
    _tap_id(app, "cmd:place:%d" % p.solution[base])
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(twin))

    batches = _record(app)
    _tap_id(app, "cmd:place:%d" % p.solution[base])          # duplicate in the row
    assert {base, twin} <= p.conflicts()
    cells = {c.name for b in batches for c in b if isinstance(c, CreateLayer)
             if c.name.startswith("cmd:cell:")}
    # both ends of the clash turn red, so both are recompiled — the new digit
    # alone is not enough
    assert cells >= {"cmd:cell:%d,%d" % sudoku.rc(k) for k in (base, twin)}

    _tap_id(app, "cmd:erase")
    assert not p.conflicts() and p.entries[twin] == 0


def test_a_digit_reaches_the_composited_frame():
    app = _app()
    i = _empty_cell(app)
    L = app.lay
    x, y = L.cell_xy(i)

    def cell_pixels():
        frame = app.service.backend.snapshot_rgba()
        return frame[y:y + L.cell, x:x + L.cell, :3].astype(int)

    before = cell_pixels()
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(i))
    _tap_id(app, "cmd:place:%d" % app.puzzle.solution[i])
    after = cell_pixels()
    assert not np.array_equal(before, after), "the digit never made it to pixels"
    # the entry colour (a light blue) is what landed there
    lit = after[(after - after.min()).sum(axis=2) > 60]
    assert len(lit) and lit[:, 2].mean() > lit[:, 0].mean(), "entry is not blue"


def test_pad_stats_follow_the_board():
    app = _app()
    i = _empty_cell(app)
    digit = app.puzzle.solution[i]
    before = app.puzzle.counts()[digit]
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(i))
    _tap_id(app, "cmd:place:%d" % digit)
    assert app.puzzle.counts()[digit] == before + 1
    assert app._counts == app.puzzle.counts(), "the pad is showing stale counts"


def test_tapping_the_same_digit_takes_it_back_out():
    app = _app()
    i = _empty_cell(app)
    digit = app.puzzle.solution[i]
    _tap_id(app, "cmd:cell:%d,%d" % sudoku.rc(i))
    _tap_id(app, "cmd:place:%d" % digit)
    _tap_id(app, "cmd:place:%d" % digit)
    assert app.puzzle.entries[i] == 0


def test_only_the_real_cells_and_digits_are_allowlisted():
    app = _app()
    # registration is the allowlist: 81 cells, 9 digits, 4 controls — no more
    assert app.dispatch.dispatch(parse_action("cmd:cell:9,9")) is False
    assert app.dispatch.dispatch(parse_action("cmd:cell:0,0")) is True
    assert app.dispatch.dispatch(parse_action("cmd:place:0")) is False
    assert app.dispatch.dispatch(parse_action("cmd:place:9")) is True
    assert app.dispatch.dispatch(parse_action("cmd:wipe")) is False
    assert app.running is True


def test_a_tap_on_nothing_is_a_no_op():
    app = _app()
    note = app.note
    _tap(app, 1, app.H - 1)                 # bare background: no layer claims it
    assert app.note == note and app.puzzle.moves == 0


def test_the_win_scrim_outranks_the_cell_keys():
    app = _app()
    while not app.puzzle.is_solved():
        _tap_id(app, "cmd:hint")            # Hint with no selection walks the board
    assert app.solved

    scrim = app.service.composer.layers["scrim"]
    cell = app.service.composer.layers["cmd:cell:0,0"]
    assert scrim.z > cell.z
    x, y = app.lay.cell_xy(0)
    assert app.service.hit_test(x + 2, y + 2) == "scrim"

    # …and the pad stays reachable beside it
    assert app.service.hit_test(*app.button_center("quit")) == "quit"

    puzzle, moves = app.puzzle, app.puzzle.moves
    _tap(app, x + 2, y + 2)
    assert app.puzzle is not puzzle and not app.solved and moves > 0
    assert "scrim" not in app.service.composer.layers


def test_quit_stops_the_loop():
    app = _app()
    assert app.running
    _tap_id(app, "quit")
    assert app.running is False


# ── the compiler contract the demo depends on ─────────────────────────────────

def test_a_transparent_button_compiles_to_a_lifted_interactive_layer():
    html = ('<screen width="200" height="120">'
            '<layer id="cells" z="10" visible="false">'
            '<button action="cell:4,7" x="10" y="20" w="30" h="30" '
            'color="#00000000">5</button></layer></screen>')
    batch = paint_scene(parse_scene(html))
    parent = next(c for c in batch if isinstance(c, CreateLayer) and c.name == "cells")
    key = next(c for c in batch if isinstance(c, CreateLayer) and c.interactive)
    assert not parent.visible and not parent.interactive
    assert key.hit_id == "cmd:cell:4,7" and key.z == parent.z + 1000
    assert (key.x, key.y, key.width, key.height) == (10, 20, 30, 30)


def test_a_component_scene_is_cut_to_its_own_size():
    app = _app()
    x, y, w, h = app.lay.panel_rect()
    batch = app._panel_batch(force=True)
    layer = next(c for c in batch if isinstance(c, CreateLayer) and c.name == "panel")
    # the panel is compiled at panel size and placed, not stamped screen-sized
    assert (layer.width, layer.height) == (w, h) < (W, H)
    assert SetPosition("panel", x, y) in batch
    assert all(isinstance(c, (CreateLayer, PlaceRawBuffer, SetPosition))
               for c in batch)


def test_layout_scales_to_the_display():
    for w, h in [(800, 480), (1024, 600), (1920, 1080)]:
        L = sudoku_demo.Layout(w, h)
        assert L.side == L.cell * 9
        assert L.bx >= 0 and L.by >= 0
        assert L.bx + L.side <= L.px, "the board runs into the panel"
        assert L.px + L.pw <= w and L.py + L.ph <= h
        assert L.cell_xy(80) == (L.bx + 8 * L.cell, L.by + 8 * L.cell)
