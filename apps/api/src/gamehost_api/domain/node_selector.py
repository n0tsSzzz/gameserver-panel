from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Node


async def least_loaded(session: AsyncSession, resources: dict[str, Any]) -> Node | None:
    req_cpu = float(resources.get("cpuCores", resources.get("cpu_cores", 1.0)))
    req_mem = int(resources.get("memMb", resources.get("mem_mb", 1024)))
    stmt = text("""
        SELECT n.id
        FROM nodes n
        LEFT JOIN (
            SELECT s.node_id,
                   SUM((s.resources->>'cpuCores')::float) AS cpu,
                   SUM((s.resources->>'memMb')::int) AS mem
            FROM servers s
            WHERE s.status IN ('provisioning','running')
            GROUP BY s.node_id
        ) used ON used.node_id = n.id
        WHERE n.status = 'online'
          AND (n.capacity_cpu - COALESCE(used.cpu, 0)) >= :req_cpu
          AND (n.capacity_mem_mb - COALESCE(used.mem, 0)) >= :req_mem
        ORDER BY (COALESCE(used.cpu, 0) / NULLIF(n.capacity_cpu::float, 0)) ASC NULLS FIRST
        LIMIT 1
    """)
    row = (await session.execute(stmt, {"req_cpu": req_cpu, "req_mem": req_mem})).first()
    if row is None:
        return None
    return await session.get(Node, row.id)
