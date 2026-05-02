from fastapi import APIRouter

from gamehost_api.api.v1 import auth

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router, prefix="/auth", tags=["auth"])
