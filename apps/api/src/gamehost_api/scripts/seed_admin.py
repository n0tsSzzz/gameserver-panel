import asyncio
import sys

import structlog

from gamehost_api.core.config import get_settings
from gamehost_api.core.logging import configure_logging
from gamehost_api.core.security import hash_password
from gamehost_api.db.session import make_engine, make_sessionmaker
from gamehost_api.repositories.users import UserRepository


async def _run() -> int:
    settings = get_settings()
    log = structlog.get_logger("seed_admin")
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        log.error("bootstrap_env_missing")
        return 1
    email = settings.bootstrap_admin_email.strip().lower()
    password = settings.bootstrap_admin_password.get_secret_value()

    engine = make_engine()
    sm = make_sessionmaker(engine)
    try:
        async with sm() as session:
            repo = UserRepository(session)
            existing = await repo.get_by_email(email)
            if existing is None:
                await repo.create(email=email, password_hash=hash_password(password), role="admin")
                await session.commit()
                log.info("created_admin", email=email)
            elif existing.role != "admin":
                existing.role = "admin"
                await session.commit()
                log.info("promoted_to_admin", email=email)
            else:
                log.info("already_admin", email=email)
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    configure_logging(get_settings().log_level)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
