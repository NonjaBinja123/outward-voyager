"""
heal_self — use the best available healing item from the pouch.
Checks for bandages first, then potions, then any food with restorative value.
Rewite me if you learn better item names.
"""


async def run(ctx) -> None:
    """Use the first healing item found in the pouch."""
    pouch = ctx.state.get("inventory", {}).get("pouch", [])
    heal_keywords = (
        "bandage", "health potion", "healing potion", "poultice",
        "antidote", "tonic", "salve", "mend",
    )
    for item in pouch:
        name = item.get("name", "").lower()
        if any(kw in name for kw in heal_keywords):
            await ctx.use_item(item["name"])
            return
    # Fallback: any food item (restores some health/food stat)
    food_keywords = ("jerky", "ration", "berry", "mushroom", "bread", "meat")
    for item in pouch:
        name = item.get("name", "").lower()
        if any(kw in name for kw in food_keywords):
            await ctx.use_item(item["name"])
            return
