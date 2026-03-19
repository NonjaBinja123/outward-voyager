def run_open_menu(state: dict) -> dict:
    return {
        "action": "open_menu",
        "params": {
            "menu_type": "inventory"
        }
    }