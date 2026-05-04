from unittest.mock import AsyncMock, patch

from httpx import AsyncClient


async def test_backup_endpoint_happy_path(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    with patch(
        "gamehost_node.api.backups.BackupOps.run_backup",
        new=AsyncMock(return_value=4096),
    ):
        r = await client.post(
            "/api/v1/containers/cid/backup",
            headers=auth_headers,
            json={"volumeName": "gh-vol", "s3Key": "k"},
        )
    assert r.status_code == 200
    assert r.json() == {"sizeBytes": 4096}


async def test_restore_endpoint_happy_path(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    with patch(
        "gamehost_node.api.backups.BackupOps.run_restore",
        new=AsyncMock(return_value=2048),
    ):
        r = await client.post(
            "/api/v1/containers/cid/restore",
            headers=auth_headers,
            json={"volumeName": "gh-vol", "s3Key": "k"},
        )
    assert r.status_code == 200
    assert r.json() == {"sizeBytes": 2048}


async def test_backup_volume_not_found(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    from gamehost_node.domain.exceptions import VolumeNotFound

    with patch(
        "gamehost_node.api.backups.BackupOps.run_backup",
        new=AsyncMock(side_effect=VolumeNotFound("gh-x")),
    ):
        r = await client.post(
            "/api/v1/containers/cid/backup",
            headers=auth_headers,
            json={"volumeName": "gh-x", "s3Key": "k"},
        )
    assert r.status_code == 404
    assert r.json()["code"] == "volume_not_found"


async def test_backup_requires_auth(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/containers/cid/backup",
        json={"volumeName": "v", "s3Key": "k"},
    )
    assert r.status_code in (401, 403)
