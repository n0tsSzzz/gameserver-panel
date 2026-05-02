from httpx import AsyncClient


async def test_healthz_no_auth(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_containers_get_without_bearer_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/containers/abc123")
    assert r.status_code == 401


async def test_containers_get_with_wrong_key_returns_401(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/containers/abc123",
        headers={"authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


async def test_containers_get_with_correct_key_returns_200(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/containers/abc123", headers=auth_headers)
    assert r.status_code == 200
