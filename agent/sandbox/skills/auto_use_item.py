"""
use_quickslot — press quickslot 1 to use whatever is assigned there.
Good for consumables the LLM has assigned to a slot.
Rewrite me once you know what items are in which slots.
"""


async def run(ctx) -> None:
    """Press quickslot 1 to use the item assigned there."""
    await ctx.game_action("quickslot_1")
