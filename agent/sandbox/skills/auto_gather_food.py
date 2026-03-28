"""
gather_nearest_item — navigate to the closest item on the ground and trigger pickup.
Use when a nearby loot item or pickup is visible in the scene objects list.
"""


async def run(ctx) -> None:
    """Navigate to the nearest non-character scene object and interact with it."""
    objects = ctx.state.get("nearby_objects", [])
    # Filter to non-characters that aren't too far
    pickups = [
        o for o in objects
        if not o.get("has_character")
        and float(o.get("distance", 999)) < 30.0
    ]
    if not pickups:
        return
    nearest = min(pickups, key=lambda o: float(o.get("distance", 999)))
    x = nearest.get("x")
    z = nearest.get("z")
    if x is not None and z is not None:
        await ctx.navigate_to(float(x), float(nearest.get("y", 0)), float(z))
        await ctx.wait(4.0)
    # Try to interact once close
    interactions = ctx.state.get("nearby_interactions", [])
    if interactions:
        nearest_i = min(interactions, key=lambda i: float(i.get("distance", 999)))
        uid = nearest_i.get("uid", "")
        if uid:
            await ctx.trigger_interaction(uid)
