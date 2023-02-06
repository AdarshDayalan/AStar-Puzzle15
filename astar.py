import Move
import heapq
import random
from pynput.keyboard import Key, Controller
import time

kb = Controller()

MOVE = Move.Move
moves = [MOVE.UP, MOVE.DOWN, MOVE.LEFT, MOVE.RIGHT]

def press(button):
    kb.press(button)
    kb.release(button)
    time.sleep(0.1)

def generateBoard():
    board = list(range(0,16))
    random.shuffle(board)
    while not isSolvable(board):
        random.shuffle(board)
    print("Starting Board:")
    displayBoard(board)
    return board

def isSolvable(board):
        parity = 0
        width = 4
        row = 0
        totalSize = width**2
        blankRow = 0

        for i in range(totalSize):
            if (i % width == 0):
                row += 1
            if (board[i] == 0):
                continue
            for j in range(i+1, totalSize):
                if (board[i] > board[j] and board[j] != 0):
                    parity += 1

        if (width % 2 == 0):
            if (blankRow % 2 == 0): return parity % 2 == 0
            return parity % 2 != 0
        
        return parity % 2 == 0

def displayBoard(board):
    row = []
    print('\n')
    for i in range(16):
        if i%4 == 0 and i != 0:
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
    newZeroPos = i+(4*dx)+dy
    board[i] = board[newZeroPos]
    board[newZeroPos] = 0
    return board

def calcHscore(board):
    hScore = 0
    for i in range(16):
        if board[i] != 0:
            tarX, tarY = (board[i]-1)//4, (board[i]-1)%4
            posX, posY = i//4, i%4
            hScore += (abs(tarX-posX) + abs(tarY-posY))**1.2
    return hScore

def getNeighbors(node):
    board = list(node)
    zI = board.index(0)
    zX, zY = zI//4, zI%4
    neighbors = []
    nMoves = []

    for m in moves:
        if not isIllegalMove(zX, zY, m):
            neighbors.append(bytes(move(board,zI,m)))
            nMoves.append(m)
    return neighbors, nMoves


def aStar(startList, end):

    cost = {}
    came_from = {}

    start = bytes(startList)

    came_from[start] = (None,None)
    cost[start] = 0
    q = [(0,start)]
    heapq.heapify(q)

    while q:
        dist, currNode = heapq.heappop(q)
        if currNode == end: break

        neighbors, nMoves = getNeighbors(currNode)

        for neighbor,m in zip(neighbors,nMoves):
            new_cost = cost[currNode] + 1
            if neighbor not in cost or new_cost < cost[neighbor]:
                cost[neighbor] = new_cost
                hDist = new_cost + calcHscore(neighbor)
                heapq.heappush(q, (hDist, neighbor))
                came_from[neighbor] = (currNode,m)
    path = []
    moveList = []
    curr = end
    while curr:
        path.append(curr)
        curr,currMove = came_from[curr]
        moveList.append(currMove)
        
    path.reverse()
    moveList.reverse()
    return path, cost, moveList

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
