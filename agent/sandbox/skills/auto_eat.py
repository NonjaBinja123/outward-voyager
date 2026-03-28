"""
eat_food — consume the first food item in the pouch to restore the food/hunger stat.
"""


async def run(ctx) -> None:
    """Eat the first food-type item found in the pouch."""
    pouch = ctx.state.get("inventory", {}).get("pouch", [])
    food_keywords = (
        "jerky", "ration", "berry", "mushroom", "bread", "meat",
        "fish", "cheese", "apple", "vegetable", "soup", "ceviche",
    )
    for item in pouch:
        name = item.get("name", "").lower()
        if any(kw in name for kw in food_keywords):
            await ctx.use_item(item["name"])
            return
