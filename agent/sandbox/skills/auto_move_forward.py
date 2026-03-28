"""
move_forward_briefly — walk forward in the current facing direction for a moment.
Useful to unstick the character or make small positional adjustments.
"""


async def run(ctx) -> None:
    """Hold forward movement for 1.5 seconds then release."""
    await ctx.game_action("move_forward", "hold")
    await ctx.wait(1.5)
    await ctx.game_action("move_forward", "release")
