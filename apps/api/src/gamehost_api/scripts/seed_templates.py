import asyncio
import sys
import uuid
from typing import Any

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gamehost_api.core.config import get_settings
from gamehost_api.core.logging import configure_logging
from gamehost_api.db.models import GameTemplate
from gamehost_api.db.session import make_engine, make_sessionmaker

_TEMPLATES: list[dict[str, Any]] = [
    {
        "slug": "minecraft-vanilla",
        "display_name": "Minecraft (Vanilla)",
        "docker_image": "itzg/minecraft-server:latest",
        "default_env": {},
        "default_ports": [{"container": 25565, "protocol": "tcp"}],
        "default_volumes": [],
        "min_resources": {"cpu": 1.0, "memMb": 2048},
    },
    {
        "slug": "valheim",
        "display_name": "Valheim",
        "docker_image": "lloesche/valheim-server:latest",
        "default_env": {},
        "default_ports": [
            {"container": 2456, "protocol": "udp"},
            {"container": 2457, "protocol": "udp"},
            {"container": 2458, "protocol": "udp"},
        ],
        "default_volumes": [],
        "min_resources": {"cpu": 2.0, "memMb": 4096},
    },
    {
        "slug": "terraria",
        "display_name": "Terraria",
        "docker_image": "ryshe/terraria:latest",
        "default_env": {},
        "default_ports": [{"container": 7777, "protocol": "tcp"}],
        "default_volumes": [],
        "min_resources": {"cpu": 1.0, "memMb": 1024},
    },
    {
        "slug": "cs2",
        "display_name": "Counter-Strike 2",
        "docker_image": "joedwards32/cs2:latest",
        "default_env": {},
        "default_ports": [
            {"container": 27015, "protocol": "tcp"},
            {"container": 27015, "protocol": "udp"},
        ],
        "default_volumes": [],
        "min_resources": {"cpu": 2.0, "memMb": 2048},
    },
    {
        "slug": "rust",
        "display_name": "Rust",
        "docker_image": "didstopia/rust-server:latest",
        "default_env": {},
        "default_ports": [
            {"container": 28015, "protocol": "udp"},
            {"container": 28016, "protocol": "tcp"},
        ],
        "default_volumes": [],
        "min_resources": {"cpu": 2.0, "memMb": 4096},
    },
]


async def _run() -> int:
    log = structlog.get_logger("seed_templates")
    engine = make_engine()
    sm = make_sessionmaker(engine)
    try:
        async with sm() as session:
            for tpl in _TEMPLATES:
                stmt = pg_insert(GameTemplate).values(id=uuid.uuid4(), is_public=True, **tpl)
                stmt = stmt.on_conflict_do_nothing(index_elements=["slug"])
                await session.execute(stmt)
            await session.commit()
        log.info("seeded_templates", count=len(_TEMPLATES))
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    configure_logging(get_settings().log_level)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
