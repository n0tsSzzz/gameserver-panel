from typing import Any
from unittest.mock import MagicMock

import docker.errors
import requests.exceptions
from httpx import AsyncClient


def _payload(name: str = "smoke") -> dict[str, Any]:
    return {
        "name": name,
        "image": "busybox:latest",
        "env": {},
        "ports": [],
        "volumes": [],
        "resources": {"cpuCores": 0.5, "memMb": 64},
        "readOnlyRoot": True,
    }


async def test_post_create_returns_201_camel(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post("/api/v1/containers", headers=auth_headers, json=_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "smoke"
    assert body["status"] == "running"
    assert "createdAt" in body


async def test_post_invalid_resources_returns_422(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    bad = _payload()
    bad["resources"]["memMb"] = 0
    r = await client.post("/api/v1/containers", headers=auth_headers, json=bad)
    assert r.status_code == 422


async def test_get_container_returns_status_and_stats(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/containers/abc123", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["stats"]["cpuPercent"] >= 0
    assert body["stats"]["memUsageMb"] >= 0


async def test_get_unknown_returns_404(
    client: AsyncClient, auth_headers: dict[str, str], mock_facade: MagicMock
) -> None:
    mock_facade.inspect.side_effect = docker.errors.NotFound("missing")
    r = await client.get("/api/v1/containers/unknown", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "container_not_found"


async def test_start_stop_restart_return_200(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for action in ("start", "stop", "restart"):
        r = await client.post(f"/api/v1/containers/abc123/{action}", headers=auth_headers)
        assert r.status_code == 200, action


async def test_delete_returns_204(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    r = await client.delete("/api/v1/containers/abc123", headers=auth_headers)
    assert r.status_code == 204


async def test_post_409_when_name_taken(
    client: AsyncClient, auth_headers: dict[str, str], mock_facade: MagicMock
) -> None:
    from unittest.mock import Mock

    err = docker.errors.APIError("conflict", response=Mock(status_code=409, reason="Conflict"))
    mock_facade.create_and_start.side_effect = err
    r = await client.post("/api/v1/containers", headers=auth_headers, json=_payload())
    assert r.status_code == 409
    assert r.json()["code"] == "container_name_taken"


async def test_connection_error_maps_to_503(
    client: AsyncClient, auth_headers: dict[str, str], mock_facade: MagicMock
) -> None:
    mock_facade.create_and_start.side_effect = requests.exceptions.ConnectionError("boom")
    r = await client.post("/api/v1/containers", headers=auth_headers, json=_payload())
    assert r.status_code == 503
    assert r.json()["code"] == "docker_unavailable"


async def test_get_logs_tail_returns_lines(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/containers/abc123/logs?tail=20", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["lines"] == ["hello", "world"]


async def test_get_logs_tail_invalid_returns_422(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/containers/abc123/logs?tail=0", headers=auth_headers)
    assert r.status_code == 422
