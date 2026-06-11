import Move
import random
from pynput.keyboard import Key, Controller
import time
import sys

kb = Controller()

MOVE = Move.Move
moves = [MOVE.UP, MOVE.DOWN, MOVE.LEFT, MOVE.RIGHT]

INVERSE = {
    MOVE.UP: MOVE.DOWN,
    MOVE.DOWN: MOVE.UP,
    MOVE.LEFT: MOVE.RIGHT,
    MOVE.RIGHT: MOVE.LEFT,
}

# Precomputed (move, dx, dy, inverse_idx) — avoid enum attribute lookup in hot loop
_MOVE_TABLE = [
    (MOVE.UP, 1, 0, 1),
    (MOVE.DOWN, -1, 0, 0),
    (MOVE.LEFT, 0, 1, 3),
    (MOVE.RIGHT, 0, -1, 2),
]

GOAL_ROW = [0] * 16
GOAL_COL = [0] * 16
for _t in range(1, 16):
    GOAL_ROW[_t] = (_t - 1) // 4
    GOAL_COL[_t] = (_t - 1) % 4

sys.setrecursionlimit(10000)

# Default goal state for the 15-puzzle (used by callers that don't supply one).
END_STATE = list(range(1, 16)) + [0]

# Map move enum -> index used by Recorder (matches _MOVE_TABLE order).
_MOVE_NAME_TO_IDX = {'UP': 0, 'DOWN': 1, 'LEFT': 2, 'RIGHT': 3}


# Auto-play timing. The 0.1 s default was a hard ceiling at 10 TPS — way
# below leaderboard solvers running 25-30 TPS. Use kb.tap() to atomically
# emit press+release, then sleep just long enough that fast web apps still
# register every keystroke. Adjust via PRESS_DELAY.
PRESS_DELAY = 0.025  # 25 ms → ~35 TPS sustained


def press(button, delay=None):
    kb.tap(button)
    d = PRESS_DELAY if delay is None else delay
    if d > 0:
        time.sleep(d)


def generateBoard():
    board = list(range(0, 16))
    random.shuffle(board)
    while not isSolvable(board):
        random.shuffle(board)
    print("Starting Board:")
    displayBoard(board)
    return board


def isSolvable(board):
    inversions = 0
    for i in range(16):
        if board[i] == 0:
            continue
        for j in range(i + 1, 16):
            if board[j] != 0 and board[i] > board[j]:
                inversions += 1
    blank_row = board.index(0) // 4
    return (inversions + blank_row) % 2 == 1


def displayBoard(board):
    row = []
    print('\n')
    for i in range(16):
        if i % 4 == 0 and i != 0:
            print(row)
            row = []
        row.append(board[i])
    print(row)
    print('\n')


def isIllegalMove(zeroX, zeroY, m):
    if m == MOVE.UP and zeroX == 3: return True
    if m == MOVE.RIGHT and zeroY == 0: return True
    if m == MOVE.DOWN and zeroX == 0: return True
    if m == MOVE.LEFT and zeroY == 3: return True
    return False


def move(oldBoard, zeroPos, dir):
    board = oldBoard.copy()
    dx, dy = dir.value
    i = zeroPos
    newZeroPos = i + (4 * dx) + dy
    board[i] = board[newZeroPos]
    board[newZeroPos] = 0
    return board


def manhattan(board):
    h = 0
    for i in range(16):
        t = board[i]
        if t == 0:
            continue
        r, c = i >> 2, i & 3
        h += abs(r - GOAL_ROW[t]) + abs(c - GOAL_COL[t])
    return h


def lc_row(board, r):
    conflicts = 0
    max_goal = -1
    base = r * 4
    for c in range(4):
        t = board[base + c]
        if t != 0 and GOAL_ROW[t] == r:
            g = GOAL_COL[t]
            if g > max_goal:
                max_goal = g
            else:
                conflicts += 1
    return conflicts


def lc_col(board, c):
    conflicts = 0
    max_goal = -1
    for r in range(4):
        t = board[r * 4 + c]
        if t != 0 and GOAL_COL[t] == c:
            g = GOAL_ROW[t]
            if g > max_goal:
                max_goal = g
            else:
                conflicts += 1
    return conflicts


def linear_conflict(board):
    return 2 * sum(lc_row(board, r) for r in range(4)) + \
           2 * sum(lc_col(board, c) for c in range(4))


def heuristic(board):
    return manhattan(board) + linear_conflict(board)


def calcHscore(board):
    return heuristic(list(board))


def getNeighbors(node):
    board = list(node)
    zI = board.index(0)
    zX, zY = zI // 4, zI % 4
    neighbors = []
    nMoves = []
    for m in moves:
        if not isIllegalMove(zX, zY, m):
            neighbors.append(bytes(move(board, zI, m)))
            nMoves.append(m)
    return neighbors, nMoves


def _ida(board, zi, g, threshold, last_inv_idx, path, h_man, h_lc, stats, my_id):
    stats['nodes'] += 1
    # Per-depth aggregate (cheap O(1) increment) — covers ALL nodes
    npd = stats['nodes_per_depth']
    if g >= len(npd):
        npd.extend([0] * (g - len(npd) + 1))
    npd[g] += 1
    # Joint depth × f-value histogram for the heatmap. Dict keyed by (depth, f).
    jdf = stats['joint_depth_f']
    key = (g, h_man + h_lc + g)
    jdf[key] = jdf.get(key, 0) + 1
    progress = stats['progress']
    # emit_every is mutated by the outer loop (adaptive throttling).
    if progress is not None and stats['nodes'] - stats['last_emit'] >= stats['emit_every']:
        stats['last_emit'] = stats['nodes']
        progress(threshold, stats['nodes'], g, h_man + h_lc, list(board), list(path))
        # Force the GIL to actually release — sleep(0) is unreliable on macOS
        # under heavy CPU load. 1 ms is the smallest reliable yield.
        time.sleep(0.001)

    f = g + h_man + h_lc
    if f > threshold:
        return f
    if h_man == 0:
        return -1

    min_next = float('inf')
    zx, zy = zi >> 2, zi & 3

    for idx in range(4):
        if idx == last_inv_idx:
            continue
        m, dx, dy, inv_idx = _MOVE_TABLE[idx]
        nx, ny = zx + dx, zy + dy
        if nx < 0 or nx > 3 or ny < 0 or ny > 3:
            continue
        ni = (nx << 2) + ny

        tile = board[ni]
        gr, gc = GOAL_ROW[tile], GOAL_COL[tile]
        old_md = abs(nx - gr) + abs(ny - gc)
        new_md = abs(zx - gr) + abs(zy - gc)
        new_h_man = h_man + new_md - old_md

        # Linear-conflict delta: only the lines containing the moved tile
        # change. Horizontal move (dx == 0) → same row, columns ny→zy.
        # Vertical move (dy == 0) → same column, rows nx→zx.
        if dx == 0:
            old_lc = lc_row(board, zx) + lc_col(board, ny) + lc_col(board, zy)
            board[zi] = tile
            board[ni] = 0
            new_lc = lc_row(board, zx) + lc_col(board, ny) + lc_col(board, zy)
        else:
            old_lc = lc_col(board, zy) + lc_row(board, nx) + lc_row(board, zx)
            board[zi] = tile
            board[ni] = 0
            new_lc = lc_col(board, zy) + lc_row(board, nx) + lc_row(board, zx)
        new_h_lc = h_lc + 2 * (new_lc - old_lc)

        # Record this child if we still have budget AND parent was recorded.
        rec = stats['record']
        child_id = None
        if my_id is not None and rec is not None and len(rec) < stats['record_max']:
            child_id = len(rec)
            rec.append((my_id, m.name, g + 1, (new_h_man + new_h_lc)))

        # Streaming binary recorder (independent of the in-memory list).
        recorder = stats['recorder']
        recorder_child_id = None
        if recorder is not None:
            recorder_child_id = recorder.record(
                stats['recorder_parent_id'],
                _MOVE_NAME_TO_IDX.get(m.name, 0),
                g + 1,
                int(new_h_man + new_h_lc),
                stats['iteration_index'],
            )
            # push onto recorded chain for solution-path tracking
            stats['recorded_chain'].append(recorder_child_id)
            # children of THIS child use recorder_child_id as parent
            prev_parent = stats['recorder_parent_id']
            stats['recorder_parent_id'] = (
                recorder_child_id if recorder_child_id is not None else prev_parent
            )

        path.append(m)
        result = _ida(board, ni, g + 1, threshold, inv_idx, path, new_h_man, new_h_lc, stats, child_id)

        if recorder is not None:
            # restore parent on backtrack or solution-found
            stats['recorder_parent_id'] = prev_parent
            if result == -1:
                # solution found: snapshot the current chain (root + descendants)
                # only on the very first unwind so we don't overwrite.
                if not stats['solution_recorder_ids']:
                    chain = [cid for cid in stats['recorded_chain'] if cid is not None]
                    # include root id 0 at the front if not already there
                    if not chain or chain[0] != 0:
                        chain = [0] + chain
                    stats['solution_recorder_ids'] = chain
            else:
                stats['recorded_chain'].pop()

        if result == -1:
            return -1

        path.pop()
        board[ni] = tile
        board[zi] = 0

        if result < min_next:
            min_next = result

    return min_next


def aStar(startList, end, progress=None, emit_every=1500,
          record=None, record_max=1500, stats_out=None, recorder=None):
    """IDA* with Manhattan + Linear Conflict (admissible -> optimal solution).

    Drop-in replacement for the original A*. Signature preserved:
        returns (path, cost, moveList)
    where cost[end] is the optimal move count and moveList[0] is None
    (legacy quirk; callers filter it).

    Optional progress callback: progress(threshold, nodes, depth, h_now,
    board_snapshot, path_snapshot) called roughly every `emit_every` nodes
    plus once per new threshold. Use it for live UI visualisation.
    """
    if isinstance(startList, (bytes, bytearray)):
        start = list(startList)
    else:
        start = list(startList)

    if isinstance(end, (bytes, bytearray)):
        end_bytes = bytes(end)
    else:
        end_bytes = bytes(end)

    board = start[:]
    h_man = manhattan(board)
    h_lc = linear_conflict(board)
    threshold = h_man + h_lc
    path = []
    zi = board.index(0)
    stats = {
        'nodes': 0,
        'progress': progress,
        'emit_every': emit_every,
        'last_emit': 0,
        'record': record,
        'record_max': record_max,
        'nodes_per_depth': [],
        'joint_depth_f': {},
        'recorder': recorder,
        'recorder_parent_id': 0 if recorder is not None else None,
        'recorded_chain': [],
        'iteration_index': 0,
        'solution_recorder_ids': [],
    }
    iterations = []  # list of (threshold, nodes_in_this_iteration)
    # Seed root in the record (parent=-1, no move)
    root_id = None
    if record is not None and record_max > 0:
        root_id = 0
        record.append((-1, None, 0, h_man + h_lc))

    if threshold == 0:
        if stats_out is not None:
            stats_out['nodes_per_depth'] = []
            stats_out['iterations'] = []
            stats_out['total_nodes'] = 0
        if recorder is not None:
            recorder.finalize([], [], [], 0)
        return [end_bytes], {end_bytes: 0}, [None]

    iter_idx = 0
    while True:
        stats['iteration_index'] = iter_idx
        nodes_before = stats['nodes']
        iter_t0 = time.time()
        if progress is not None:
            progress(threshold, stats['nodes'], 0, h_man + h_lc, list(board), [])
        result = _ida(board, zi, 0, threshold, -1, path, h_man, h_lc, stats, root_id)
        iter_nodes = stats['nodes'] - nodes_before
        iter_elapsed = max(1e-6, time.time() - iter_t0)
        iterations.append((threshold, iter_nodes))
        # Adaptive throttling: target ~30 emits/sec regardless of solver speed.
        # For fast solves emit_every stays low; for huge solves it scales way up
        # so the 1 ms GIL-yield doesn't dominate.
        rate = iter_nodes / iter_elapsed  # nodes/sec
        stats['emit_every'] = max(500, min(2_000_000, int(rate / 30)))
        if result == -1:
            if stats_out is not None:
                stats_out['nodes_per_depth'] = list(stats['nodes_per_depth'])
                stats_out['iterations'] = iterations
                stats_out['total_nodes'] = stats['nodes']
                stats_out['joint_depth_f'] = dict(stats['joint_depth_f'])
            cost_dict = {end_bytes: len(path)}
            move_list = [None] + list(path)
            if recorder is not None:
                solution_move_names = [m.name for m in path]
                recorder.mark_solution_path(stats['solution_recorder_ids'])
                recorder.finalize(
                    solution_move_names,
                    iterations,
                    list(stats['nodes_per_depth']),
                    stats['nodes'],
                )
            return [end_bytes], cost_dict, move_list
        if result == float('inf'):
            if recorder is not None:
                recorder.finalize([], iterations,
                                  list(stats['nodes_per_depth']),
                                  stats['nodes'])
            return [], {}, []
        threshold = result
        iter_idx += 1


def runMoves(moves, delay=None):
    """Replay a sequence of moves via the keyboard. Returns elapsed seconds.

    delay overrides PRESS_DELAY for this run. 0 = absolute max TPS.
    """
    key_for = {MOVE.UP: Key.up, MOVE.DOWN: Key.down,
               MOVE.LEFT: Key.left, MOVE.RIGHT: Key.right}
    t0 = time.time()
    for m in moves:
        k = key_for.get(m)
        if k is not None:
            press(k, delay)
    return time.time() - t0
