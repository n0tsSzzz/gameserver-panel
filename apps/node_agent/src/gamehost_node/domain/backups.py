from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from botocore.exceptions import ClientError
from docker.errors import APIError, NotFound

from gamehost_node.core.config import get_settings
from gamehost_node.docker_facade import DockerFacade
from gamehost_node.domain.exceptions import S3Unavailable, VolumeNotFound
from gamehost_node.s3_client import s3_client_ctx

_HELPER_IMAGE = "busybox:latest"
_PART_SIZE = 8 * 1024 * 1024  # 8 MiB


def _client(facade: DockerFacade) -> Any:
    return facade._client  # internal use


async def _ensure_volume(facade: DockerFacade, volume_name: str) -> None:
    try:
        await asyncio.to_thread(_client(facade).volumes.get, volume_name)
    except NotFound as exc:
        raise VolumeNotFound(volume_name) from exc


async def _pull_helper(facade: DockerFacade) -> None:
    try:
        await asyncio.to_thread(_client(facade).images.get, _HELPER_IMAGE)
    except (NotFound, APIError):
        await asyncio.to_thread(_client(facade).images.pull, _HELPER_IMAGE)


class BackupOps:
    def __init__(self, facade: DockerFacade) -> None:
        self._facade = facade
        self._settings = get_settings()

    async def run_backup(self, *, volume_name: str, s3_key: str) -> int:
        """Pipe `tar -cz /data` from a busybox helper into MinIO at `s3_key`.

        Returns total bytes uploaded.
        """
        await _ensure_volume(self._facade, volume_name)
        await _pull_helper(self._facade)

        bucket = self._settings.s3_bucket
        client_obj = _client(self._facade)
        helper = await asyncio.to_thread(
            client_obj.containers.create,
            image=_HELPER_IMAGE,
            command=["sh", "-c", "tar -cf - -C /data . | gzip"],
            volumes={volume_name: {"bind": "/data", "mode": "ro"}},
            stdin_open=False,
            tty=False,
            detach=True,
        )
        try:
            socket = await asyncio.to_thread(
                helper.attach_socket,
                params={"stdout": 1, "stderr": 0, "stream": 1},
            )
            await asyncio.to_thread(helper.start)
            total = await self._upload_stream_to_s3(socket, bucket, s3_key)
            await asyncio.to_thread(helper.wait, timeout=60)
            return total
        finally:
            with contextlib.suppress(APIError):
                await asyncio.to_thread(helper.remove, force=True)

    async def run_restore(self, *, volume_name: str, s3_key: str) -> int:
        """Stream `s3_key` into a busybox helper that runs `tar -xzf -` into the
        named volume. Returns bytes downloaded."""
        await _ensure_volume(self._facade, volume_name)
        await _pull_helper(self._facade)

        bucket = self._settings.s3_bucket
        client_obj = _client(self._facade)
        helper = await asyncio.to_thread(
            client_obj.containers.create,
            image=_HELPER_IMAGE,
            command=["sh", "-c", "tar -xzf - -C /data"],
            volumes={volume_name: {"bind": "/data", "mode": "rw"}},
            stdin_open=True,
            tty=False,
            detach=True,
        )
        try:
            socket = await asyncio.to_thread(
                helper.attach_socket,
                params={"stdin": 1, "stream": 1},
            )
            await asyncio.to_thread(helper.start)
            total = await self._download_stream_from_s3(socket, bucket, s3_key)
            with contextlib.suppress(Exception):
                socket._sock.shutdown(1)
            await asyncio.to_thread(helper.wait, timeout=120)
            return total
        finally:
            with contextlib.suppress(APIError):
                await asyncio.to_thread(helper.remove, force=True)

    async def _upload_stream_to_s3(self, docker_socket: Any, bucket: str, key: str) -> int:
        try:
            async with s3_client_ctx() as s3:
                mpu = await s3.create_multipart_upload(Bucket=bucket, Key=key)
                upload_id = mpu["UploadId"]
                parts: list[dict[str, Any]] = []
                part_number = 1
                buf = bytearray()
                total = 0
                try:
                    while True:
                        chunk = await asyncio.to_thread(docker_socket._sock.recv, 65536)
                        if not chunk:
                            break
                        buf.extend(chunk)
                        if len(buf) >= _PART_SIZE:
                            part = await s3.upload_part(
                                Bucket=bucket,
                                Key=key,
                                PartNumber=part_number,
                                UploadId=upload_id,
                                Body=bytes(buf),
                            )
                            parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                            total += len(buf)
                            part_number += 1
                            buf.clear()
                    if buf or not parts:
                        part = await s3.upload_part(
                            Bucket=bucket,
                            Key=key,
                            PartNumber=part_number,
                            UploadId=upload_id,
                            Body=bytes(buf),
                        )
                        parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                        total += len(buf)
                    await s3.complete_multipart_upload(
                        Bucket=bucket,
                        Key=key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
                    return total
                except Exception:
                    with contextlib.suppress(Exception):
                        await s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
                    raise
        except ClientError as exc:
            raise S3Unavailable(str(exc)) from exc

    async def _download_stream_from_s3(self, docker_socket: Any, bucket: str, key: str) -> int:
        try:
            async with s3_client_ctx() as s3:
                obj = await s3.get_object(Bucket=bucket, Key=key)
                body = obj["Body"]
                total = 0
                async for chunk in body.iter_chunks(65536):
                    if not chunk:
                        continue
                    await asyncio.to_thread(docker_socket._sock.sendall, chunk)
                    total += len(chunk)
                return total
        except ClientError as exc:
            raise S3Unavailable(str(exc)) from exc
