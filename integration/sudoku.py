"""Sudoku model — the game, with no screen in it.

Pure logic: no ``drm_*`` imports, no Pillow, no coordinates.  The demo
(:mod:`sudoku_demo`) owns every pixel; this module owns the rules.  That split
mirrors the stack itself — ``drm_composer`` compiles scenes and never blends,
this module decides the game and never draws.

A grid is a flat list of 81 ints, row-major, ``0`` meaning empty:

    >>> p = generate(clues=32, seed=7)
    >>> p.clues                                  # givens the player cannot edit
    32
    >>> p.set(next(i for i in range(81) if not p.givens[i]), 5)
    True

The solver is a plain bitmask backtracker with most-constrained-cell first —
fast enough that generation (which solves a few hundred times to prove
uniqueness) is well under a second.
"""

import random

N = 9
CELLS = N * N
_ALL = 0x1FF                     # bitmask of the digits 1..9

DIFFICULTIES = {"easy": 45, "medium": 32, "hard": 26}


def box_of(i: int) -> int:
    """Index of the 3x3 box holding cell `i` (0..8, left-to-right, top-down)."""
    return (i // 27) * 3 + (i % 9) // 3


def rc(i: int) -> tuple[int, int]:
    return divmod(i, N)


def idx(r: int, c: int) -> int:
    return r * N + c


# The 27 groups — 9 rows, 9 columns, 9 boxes.  Every rule in sudoku is "no digit
# twice in a group", so conflict detection is one walk over this table.
GROUPS: tuple[tuple[int, ...], ...] = (
    tuple(tuple(idx(r, c) for c in range(N)) for r in range(N))
    + tuple(tuple(idx(r, c) for r in range(N)) for c in range(N))
    + tuple(tuple(idx(b // 3 * 3 + dr, b % 3 * 3 + dc)
                  for dr in range(3) for dc in range(3)) for b in range(N))
)


# ── solver ────────────────────────────────────────────────────────────────────

def solutions(grid, limit: int = 1, shuffle=None) -> list[list[int]]:
    """Solve `grid`, returning at most `limit` solutions.

    `limit=1` asks "is it solvable"; `limit=2` asks "is it *uniquely* solvable"
    (the question the generator needs).  Pass `shuffle` (e.g. ``rng.shuffle``) to
    try candidate digits in random order — that turns solving an empty grid into
    generating a random complete one.
    """
    g = list(grid)
    rows, cols, boxes = [0] * N, [0] * N, [0] * N
    for i, v in enumerate(g):
        if not v:
            continue
        bit = 1 << (v - 1)
        r, c, b = i // N, i % N, box_of(i)
        if (rows[r] | cols[c] | boxes[b]) & bit:
            return []            # the grid already contradicts itself
        rows[r] |= bit
        cols[c] |= bit
        boxes[b] |= bit

    found: list[list[int]] = []

    def step() -> None:
        # Most-constrained cell first: the fewer candidates, the shallower the
        # search tree.  A cell with none at all is a dead end — back out now.
        best, best_mask, best_n = -1, 0, N + 1
        for i, v in enumerate(g):
            if v:
                continue
            free = ~(rows[i // N] | cols[i % N] | boxes[box_of(i)]) & _ALL
            n = free.bit_count()
            if n == 0:
                return
            if n < best_n:
                best, best_mask, best_n = i, free, n
                if n == 1:
                    break
        if best < 0:                     # nothing empty left: a full solution
            found.append(list(g))
            return

        r, c, b = best // N, best % N, box_of(best)
        cands = [d for d in range(1, N + 1) if best_mask >> (d - 1) & 1]
        if shuffle is not None:
            shuffle(cands)
        for d in cands:
            bit = 1 << (d - 1)
            g[best] = d
            rows[r] |= bit; cols[c] |= bit; boxes[b] |= bit
            step()
            g[best] = 0
            rows[r] &= ~bit; cols[c] &= ~bit; boxes[b] &= ~bit
            if len(found) >= limit:
                return

    step()
    return found


def solve(grid) -> list[int] | None:
    """One solution for `grid`, or None if it has none."""
    got = solutions(grid, limit=1)
    return got[0] if got else None


def is_unique(grid) -> bool:
    """True if `grid` has exactly one solution."""
    return len(solutions(grid, limit=2)) == 1


# ── generation ────────────────────────────────────────────────────────────────

def _complete_grid(rng) -> list[int]:
    """A random complete, valid grid — an empty grid solved in random order."""
    return solutions([0] * CELLS, limit=1, shuffle=rng.shuffle)[0]


def _dig(solution, clues: int, rng) -> list[int]:
    """Remove cells from `solution` while the puzzle stays uniquely solvable.

    Cells go in *mirror pairs* (`i` with `80 - i`), the symmetry newspapers use:
    it costs nothing and the finished grid looks deliberate rather than random.
    A removal that would admit a second solution is put back.
    """
    grid = list(solution)
    pairs = sorted({(min(i, CELLS - 1 - i), max(i, CELLS - 1 - i))
                    for i in range(CELLS)})
    rng.shuffle(pairs)
    remaining = CELLS
    for a, b in pairs:
        group = (a,) if a == b else (a, b)
        if remaining - len(group) < clues:
            continue
        saved = [grid[i] for i in group]
        for i in group:
            grid[i] = 0
        if is_unique(grid):
            remaining -= len(group)
        else:
            for i, v in zip(group, saved):
                grid[i] = v
    return grid


def generate(clues: int = 32, seed=None, rng=None) -> "Puzzle":
    """A uniquely-solvable puzzle with roughly `clues` givens.

    `seed` (or a supplied `rng`) makes generation reproducible, which is what
    lets the tests and the scripted selftest play a *known* board.  The clue
    count is a target, not a promise: symmetry and the uniqueness constraint can
    leave a few more.
    """
    rng = rng or random.Random(seed)
    solution = _complete_grid(rng)
    return Puzzle(_dig(solution, clues, rng), solution)


# ── the playable board ────────────────────────────────────────────────────────

class Puzzle:
    """Givens (immutable), the player's entries, and the questions a UI asks.

    Givens and entries are kept in *separate* grids on purpose: the demo paints
    them differently, and "can I edit this cell" then needs no extra bookkeeping.
    """

    def __init__(self, givens, solution):
        self.givens = list(givens)
        self.solution = list(solution)
        self.entries = [0] * CELLS
        self.moves = 0

    # ── queries ───────────────────────────────────────────────────────────────

    @property
    def clues(self) -> int:
        return CELLS - self.givens.count(0)

    def value(self, i: int) -> int:
        """The digit shown in cell `i` — given, else entry, else 0."""
        return self.givens[i] or self.entries[i]

    def is_given(self, i: int) -> bool:
        return self.givens[i] != 0

    def grid(self) -> list[int]:
        return [self.value(i) for i in range(CELLS)]

    def filled(self) -> int:
        return CELLS - self.grid().count(0)

    def counts(self) -> dict[int, int]:
        """digit -> how many times it is on the board (givens + entries)."""
        grid = self.grid()
        return {d: grid.count(d) for d in range(1, N + 1)}

    def conflicts(self) -> set[int]:
        """Cells whose digit repeats inside a row, column, or box.

        Every member of a clash is returned, givens included — the player needs
        to see *both* ends of a mistake, not just the half they typed.
        """
        bad: set[int] = set()
        grid = self.grid()
        for group in GROUPS:
            seen: dict[int, int] = {}
            for i in group:
                v = grid[i]
                if not v:
                    continue
                if v in seen:
                    bad.add(i)
                    bad.add(seen[v])
                else:
                    seen[v] = i
        return bad

    def is_solved(self) -> bool:
        return self.filled() == CELLS and not self.conflicts()

    # ── moves (givens are never writable) ─────────────────────────────────────

    def set(self, i: int, digit: int) -> bool:
        """Write `digit` into cell `i`. False (no change) if the cell is a given."""
        if self.is_given(i):
            return False
        if self.entries[i] == digit:
            return True
        self.entries[i] = digit
        self.moves += 1
        return True

    def clear(self, i: int) -> bool:
        return self.set(i, 0)

    def hint(self, i: int) -> int | None:
        """Reveal the solution digit for cell `i` (None if it is a given)."""
        if self.is_given(i):
            return None
        self.set(i, self.solution[i])
        return self.solution[i]
