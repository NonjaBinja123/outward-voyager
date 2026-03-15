import math
import time
from dataclasses import dataclass
from typing import Dict

@dataclass
class MoveForwardParams:
    speed: float = 1.0

def run_move_forward(state: Dict) -> Dict[str, Dict]:
    move_forward_params = {'speed': state['stamina'] * 0.5}
    return {'action': 'move_forward', 'params': move_forward_params}