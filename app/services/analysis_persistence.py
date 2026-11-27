import asyncio


async def finish_analysis_write(write):
    """Let an in-flight database write settle before propagating cancellation."""
    operation = asyncio.ensure_future(write)
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        await asyncio.gather(operation, return_exceptions=True)
        raise
