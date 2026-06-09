from astar import aStar, runMoves, generateBoard
import time

endState = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,0]
startState = [12,14,10,13,1,5,2,3,7,0,11,6,8,9,4,5]
# startState = generateBoard()

end = bytes(endState)
start = bytes(startState)

print("Starting...")
path, cost, moveList = aStar(start, end)

print("Best Path in",cost[end], "moves")
time.sleep(3)
runMoves(moveList)
