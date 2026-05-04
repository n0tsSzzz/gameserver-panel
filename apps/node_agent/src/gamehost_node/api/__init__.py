from fastapi import APIRouter

from gamehost_node.api import backups, containers, health

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(containers.router, prefix="/containers", tags=["containers"])
api_v1.include_router(backups.router, prefix="/containers", tags=["backups"])

# /healthz lives at root, mounted directly in main.py
__all__ = ["api_v1", "health"]
