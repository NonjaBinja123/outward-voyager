import random
from typing import Dict, Any

def run_heal(state: Dict[str, Any]) -> Dict[str, Any]:
    if state['health'] == 100:
        return {'action': 'heal', 'params': {}}
    action_params = {
        'action': 'heal',
        'location': random.choice(['familiar_location1', 'familiar_location2']),
        'amount': random.randint(10, 20),
        'stamina': state['stamina'] * 0.8,
        'rest_time': 60 if random.random() < 0.7 else 120
    }
    return action_params