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


def press(button):
    kb.press(button)
    kb.release(button)
    time.sleep(0.1)


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


def _ida(board, zi, g, threshold, last_inv_idx, path, h_man, h_lc):
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

        path.append(m)
        result = _ida(board, ni, g + 1, threshold, inv_idx, path, new_h_man, new_h_lc)

        if result == -1:
            return -1

        path.pop()
        board[ni] = tile
        board[zi] = 0

        if result < min_next:
            min_next = result

    return min_next


def aStar(startList, end):
    """IDA* with Manhattan + Linear Conflict (admissible -> optimal solution).

    Drop-in replacement for the original A*. Signature preserved:
        returns (path, cost, moveList)
    where cost[end] is the optimal move count and moveList[0] is None
    (legacy quirk; callers filter it).
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

    if threshold == 0:
        return [end_bytes], {end_bytes: 0}, [None]

    while True:
        result = _ida(board, zi, 0, threshold, -1, path, h_man, h_lc)
        if result == -1:
            cost_dict = {end_bytes: len(path)}
            move_list = [None] + list(path)
            return [end_bytes], cost_dict, move_list
        if result == float('inf'):
            return [], {}, []
        threshold = result


def runMoves(moves):
    for m in moves:
        if m == MOVE.UP:
            press(Key.up)
        if m == MOVE.DOWN:
            press(Key.down)
        if m == MOVE.LEFT:
            press(Key.left)
        if m == MOVE.RIGHT:
            press(Key.right)
