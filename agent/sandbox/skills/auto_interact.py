def run_interact(state: dict) -> dict:
    """
    Interact with the nearby 'Interiors' interaction point to enter the kitchen building.
    This is triggered when the agent has Hungry and Parched status effects and needs
    to press F (interact) to use the kitchen to find food or water.
    """
    return {
        "action": "interact",
        "params": {
            "key": "F",
            "target": "Interiors",
            "reason": "Entering kitchen to find food and water due to Hungry and Parched status effects"
        }
    }