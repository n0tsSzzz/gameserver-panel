from typing import Any

from gamehost_worker.jobs._lifecycle import run_lifecycle


async def restart(ctx: dict[str, Any], task_id_str: str) -> None:
    await run_lifecycle(ctx, task_id_str, "restart")
