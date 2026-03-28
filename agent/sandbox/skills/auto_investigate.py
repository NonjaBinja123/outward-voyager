"""
investigate_area — open inventory then map to get bearings, then close both.
Good for orienting after arriving somewhere new or after a scene transition.
"""


async def run(ctx) -> None:
    """Briefly open inventory, then open map to orient, then close."""
    await ctx.game_action("toggle_inventory")
    await ctx.wait(2.0)
    await ctx.game_action("toggle_inventory")
    await ctx.wait(0.5)
    await ctx.game_action("toggle_map")
    await ctx.wait(2.0)
    await ctx.game_action("toggle_map")
