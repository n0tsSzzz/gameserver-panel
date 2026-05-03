from fastapi import APIRouter

from gamehost_api.api.v1 import auth, nodes, servers, tasks, templates

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router, prefix="/auth", tags=["auth"])
api_v1.include_router(templates.router, prefix="/templates", tags=["templates"])
api_v1.include_router(nodes.router, prefix="/nodes", tags=["nodes"])
api_v1.include_router(servers.router, prefix="/servers", tags=["servers"])
api_v1.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
