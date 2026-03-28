"""
explore_inventory — open the inventory menu and close it.
Use this to get a fresh look at what items the character is carrying.
After running, request_vision to read the menu contents.
"""


async def run(ctx) -> None:
    """Toggle inventory open, wait for it to render, then close it."""
    await ctx.game_action("toggle_inventory")
    await ctx.wait(1.5)
    await ctx.game_action("toggle_inventory")
