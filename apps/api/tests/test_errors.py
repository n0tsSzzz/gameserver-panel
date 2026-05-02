from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from gamehost_api.core.errors import register_exception_handlers
from gamehost_api.domain.exceptions import EmailAlreadyTaken


async def test_domain_error_serialized_as_problem_details() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise EmailAlreadyTaken("a@b.test")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/boom")

    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Email is already registered"
    assert body["status"] == 409
    assert body["code"] == "email_taken"
    assert body["detail"] == "a@b.test"


async def test_validation_error_serialized_as_problem_details() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    class In(BaseModel):
        x: int

    @app.post("/echo")
    async def echo(payload: In) -> dict[str, int]:
        return {"x": payload.x}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/echo", json={"x": "not-an-int"})

    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert "errors" in body
