from enum import Enum

class Move(Enum):
    UP = 1,0
    DOWN = -1,0
    LEFT = 0,1
    RIGHT = 0,-1