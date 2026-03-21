def run_interact(state: dict) -> dict:
    """
    Interact with the nearby 'Interiors' object (likely the lighthouse door)
    at approximately 10.4m distance.
    """
    return {
        "action": "interact",
        "params": {
            "target": "Interiors",
            "intent": "open_door",
            "description": "Interacting with the lighthouse door (Interiors) at ~10.4m"
        }
    }