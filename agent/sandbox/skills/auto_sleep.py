"""
find_and_rest — look for a bedroll or campfire in nearby interactions and trigger it.
Sleep/rest restores Food, Drink, and Sleep stats over time.
"""


async def run(ctx) -> None:
    """Find a rest point nearby (bed, bedroll, campfire) and interact with it."""
    rest_keywords = ("bed", "bedroll", "campfire", "fire", "tent", "inn", "sleep")
    interactions = ctx.state.get("nearby_interactions", [])
    for obj in interactions:
        name = obj.get("label", obj.get("name", "")).lower()
        if any(kw in name for kw in rest_keywords):
            uid = obj.get("uid", "")
            x = obj.get("x")
            z = obj.get("z")
            if x is not None and z is not None:
                await ctx.navigate_to(float(x), float(obj.get("y", 0)), float(z))
                await ctx.wait(3.0)
            if uid:
                await ctx.trigger_interaction(uid)
            return
