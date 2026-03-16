from dataclasses import dataclass
import random
import math
import time

@dataclass
class MoveDirection:
    x: float
    y: float

def run_move_forward(state: dict) -> dict:
    stamina = state.get('stamina', 100)
    
    if stamina < 0 or stamina > 100:
        return {'action': 'error', 'params': {'message': 'Invalid stamina level'}}

    direction = MoveDirection(math.cos(time.time()), math.sin(time.time()))
    speed = 1.5
    distance = 10

    move_direction = {
        'x': round(direction.x * speed, 2),
        'y': round(direction.y * speed, 2)
    }

    return {'action': 'move_forward', 'params': move_direction}