"""
drink_water — consume the first drinkable item in the pouch to restore thirst.
"""


async def run(ctx) -> None:
    """Drink the first drinkable item found in the pouch."""
    pouch = ctx.state.get("inventory", {}).get("pouch", [])
    drink_keywords = (
        "waterskin", "water", "tea", "brew", "juice", "flask",
        "potion", "elixir", "tonic",
    )
    for item in pouch:
        name = item.get("name", "").lower()
        if any(kw in name for kw in drink_keywords):
            await ctx.use_item(item["name"])
            return
