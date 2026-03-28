"""
combat_dodge_attack — dodge sideways to avoid an attack, then immediately counter.
Works best when an enemy swing is incoming. Tune wait time if attack misses.
"""


async def run(ctx) -> None:
    """Dodge, wait for dodge animation, then attack while enemy is recovering."""
    await ctx.game_action("dodge")
    await ctx.wait(0.45)
    await ctx.game_action("attack")
