from astar import aStar, runMoves, generateBoard
import time

endState = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,0]
startState = [13,2,5,15,1,4,11,9,6,14,3,7,10,12,0,8]
# startState = generateBoard()

end = bytes(endState)
start = bytes(startState)

path, cost, moveList = aStar(start, end)

print("Best Path in",cost[end], "moves")
# time.sleep(3)
# runMoves(moveList)
