"""
move_forward_briefly — walk forward for a moment.
Useful to unstick the character or make small positional adjustments.
"""


async def run(ctx) -> None:
    """Walk forward for 1.5 seconds."""
    await ctx.move("forward", 1.5)
