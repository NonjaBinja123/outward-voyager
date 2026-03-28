"""
approach_and_interact — navigate to the nearest interactable object and trigger it.
Use when there is exactly one obvious thing to interact with nearby.
"""


async def run(ctx) -> None:
    """Navigate to nearest interactable (>2m away), then trigger it."""
    interactions = ctx.state.get("nearby_interactions", [])
    candidates = [i for i in interactions if float(i.get("distance", 999)) >= 2.0]
    if not candidates:
        return
    nearest = min(candidates, key=lambda i: float(i.get("distance", 999)))
    x = nearest.get("x")
    z = nearest.get("z")
    uid = nearest.get("uid", "")
    if x is not None and z is not None:
        await ctx.navigate_to(float(x), float(nearest.get("y", 0)), float(z))
        await ctx.wait(3.0)
    if uid:
        await ctx.trigger_interaction(uid)
