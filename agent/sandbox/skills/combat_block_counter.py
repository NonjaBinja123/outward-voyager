"""
combat_block_counter — hold block to absorb an incoming hit, then counter-attack.
Classic block-and-punish sequence. Rewrite timing based on observed results.
"""


async def run(ctx) -> None:
    """Hold block, absorb hit, release, then attack."""
    await ctx.game_action("block", "hold")
    await ctx.wait(0.8)
    await ctx.game_action("block", "release")
    await ctx.wait(0.1)
    await ctx.game_action("attack")
