# A* 15-Puzzle Solver

A Python A* solver for the classic 15-puzzle, with a Tkinter GUI that can:

- **Paste a screenshot** of any 15-puzzle and auto-detect the board via OCR
- **Play live** with the arrow keys inside the GUI
- **Solve** any solvable board with optimal A* (IDA* + Manhattan + Linear Conflict)
- **Animate** the solution on the in-app board
- **Auto-play** the solution by sending real arrow-key presses to any other window (e.g. the [puzzle in your browser](https://15puzzle.netlify.app/))

## Quick Start

```bash
pip install pynput pillow pytesseract
brew install tesseract            # macOS — required for image paste / OCR
brew install python-tk            # macOS — only if your Python lacks Tk

python3 gui.py
```

For macOS, the auto-play feature also needs **Accessibility permission** for your terminal: System Settings → Privacy & Security → Accessibility.

## Using the GUI

| Action | How |
| --- | --- |
| Load a puzzle from a screenshot | Click **Paste** (or ⌘V) |
| Edit a tile | Click it, type a number |
| Play manually | Arrow keys |
| Solve | **Solve** button or ↵ |
| Animate the solution on the board | **Animate** |
| Auto-play the solution to another window | **Auto-play** (5-second countdown — switch to the puzzle tab) |
| Shuffle | **Shuffle** |
| Reset to solved state | **Reset** |

## Algorithm

The solver uses **IDA*** (Iterative-Deepening A*) with an admissible heuristic:

> `h(n) = manhattan_distance(n) + 2 × linear_conflict(n)`

Linear conflict adds 2 moves for every pair of tiles that are in their goal row/column but in the wrong order — Manhattan distance alone cannot see those forced detours, so adding them gives a much tighter (still admissible) lower bound. IDA* is used instead of vanilla A* because its O(depth) memory keeps the solver fast on hard scrambles where A*'s open set explodes.

The heuristic is updated incrementally on each move (only the affected row/column's linear conflict is recomputed), which keeps a typical 40–50 move scramble well under a second in pure Python.

## Algorithm — Technical Deep Dive

### State space and branching

The 15-puzzle has 16!/2 ≈ **10.5 trillion** reachable states (half of all permutations are unreachable due to parity). The search graph has a branching factor of ~2.13 — the blank has up to 4 neighbors, minus the one we just came from, averaged over edge/corner cells. Optimal solutions can be up to **80 moves** for the worst scrambles, so a blind BFS is hopeless.

### A* vs Dijkstra

Both expand nodes in order of a priority. Dijkstra orders by `g(n)` — the cost from start to `n` — so it fans out uniformly in every direction like ripples. A* orders by `f(n) = g(n) + h(n)`, where `h(n)` *estimates* the remaining cost to the goal, steering the search toward states that look closer. If `h` is **admissible** (never overestimates), A* is guaranteed to find an optimal path.

### Why IDA* instead of A*

Vanilla A* keeps every visited state in an open set + closed dictionary. On hard 15-puzzle scrambles this grows to millions of entries — Python's per-state overhead (`dict` lookups, heap operations, `bytes`/`tuple` keys) makes it slow and memory-hungry.

**IDA*** flips the trade-off:

```
threshold = h(start)
loop:
    result = dfs(start, g=0, threshold)
    if result == FOUND: return path
    threshold = result   # smallest f-value that exceeded the bound
```

Each iteration is a depth-first search bounded by the current `f`-threshold. If a branch exceeds the bound, it's pruned and the algorithm records the smallest `f` it saw above the bound — that becomes the next iteration's threshold. This gives:

- **O(depth) memory** instead of O(states-visited)
- **No heap, no closed set** — just recursion and the path stack
- Re-expands states across iterations, but the geometric growth of the threshold means the final iteration dominates total work

### Heuristic 1 — Manhattan distance

For every tile, sum the L1 distance between its current cell and its goal cell:

```
h_manhattan(s) = Σ |row(tile) - goal_row(tile)| + |col(tile) - goal_col(tile)|
```

Admissible because each move slides exactly one tile by one cell, so a tile that is `d` cells from home requires at least `d` moves to get home.

### Heuristic 2 — Linear conflict

Manhattan ignores blocking. Two tiles can both be in their correct row but need to *pass through each other*, which requires extra moves. Specifically: if tiles A and B are in their goal row, and A is left of B but A's goal column is right of B's goal column, then one of them must temporarily leave the row and come back. That's **2 extra moves** Manhattan can't see. Same logic for goal columns.

The implementation walks each row/column once, tracking the maximum goal-column seen so far among tiles whose goal row matches the current row. Any tile whose goal column is *less* than that maximum is a conflict:

```python
# astar.py — lc_row(board, r)
max_goal = -1
for c in range(4):
    t = board[r*4 + c]
    if t != 0 and GOAL_ROW[t] == r:
        if GOAL_COL[t] > max_goal: max_goal = GOAL_COL[t]
        else: conflicts += 1
```

The full heuristic adds `2 × (row_conflicts + col_conflicts)` to Manhattan and is provably still admissible (each conflict really does cost ≥2 extra).

### Incremental heuristic updates

Recomputing `h` from scratch at every node would be ~50 operations per expansion — multiplied by millions of nodes, that's the entire runtime. So both pieces are updated incrementally:

**Manhattan delta** when a tile slides from `(nx, ny)` into the blank at `(zx, zy)`:

```
old_md = |nx - goal_row[t]| + |ny - goal_col[t]|
new_md = |zx - goal_row[t]| + |zy - goal_col[t]|
new_h_man = h_man + (new_md - old_md)
```

Only the moved tile's contribution changes — everything else is invariant.

**Linear conflict delta**: only the row/column containing the moved tile can change. For a horizontal move (`dx == 0`) we recompute LC for one row and two columns; for a vertical move, one column and two rows. The rest of the board is untouched.

This brings per-node cost from ~50 to a handful of arithmetic ops.

### Search-tree pruning

- **No inverse moves.** If we just moved a tile UP into the blank, immediately moving the same tile DOWN would just undo it. The recursion passes the index of the move-to-skip (`last_inv_idx`) so the loop skips it without any work. This roughly halves the branching factor.
- **Move table is precomputed** as `(MOVE, dx, dy, inverse_index)` tuples — avoids the per-iteration `enum.value` attribute lookup that otherwise dominates the hot loop.
- **In-place board mutation** with undo on backtrack — no list copies per expansion.

### Solvability

Not every permutation is reachable. For an even-width board (4×4) with the goal blank at the bottom-right, a state is solvable iff:

> `(inversions + blank_row_from_top)` is **odd**

where *inversions* counts pairs `(i, j)` with `i < j` and `tile_i > tile_j > 0`. The GUI runs this check before solving so it can warn instead of spinning forever on an unsolvable board.

### Performance

| Scramble difficulty | Optimal solution | Solve time (Python 3.14, M-series Mac) |
| --- | --- | --- |
| Easy (~25 moves) | 25 | < 0.1 s |
| Typical random | 40–50 | 1–10 s |
| Hard random | 50–55 | 10–20 s |
| Near-worst (~60 moves) | 55–60 | 20–60 s |

For sub-second worst-case solves you'd swap in **pattern databases** (precomputed exact heuristics for 6-7 tile subsets) — that's the standard escalation if this isn't fast enough.

## CLI Usage

`main.py` runs a hard-coded scramble:

```bash
python3 main.py
```

You can also use the algorithm directly:

```python
from astar import aStar

end   = bytes([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,0])
start = bytes([13,2,5,15,1,4,11,9,6,14,3,7,10,12,0,8])

_path, cost, moves = aStar(start, end)
print(f"Solved in {cost[end]} moves")
```

## Demo

https://user-images.githubusercontent.com/39389186/217068826-51b8930b-ecea-4bef-a596-7ee93ccd990f.mov

Play the puzzle yourself → https://15puzzle.netlify.app/  
(Website credits: Shubham Singh)

## Files

| File | Purpose |
| --- | --- |
| `gui.py` | Main app — board, paste/OCR, play, solve, animate, auto-play |
| `astar.py` | IDA* solver + heuristic + keyboard playback |
| `main.py` | Headless CLI example |
| `Move.py` | Move enum used by the solver |
