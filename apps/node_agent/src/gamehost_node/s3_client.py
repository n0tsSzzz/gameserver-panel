from __future__ import annotations

from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from gamehost_node.core.config import get_settings


def make_session() -> aioboto3.Session:
    s = get_settings()
    return aioboto3.Session(
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key.get_secret_value(),
        region_name=s.s3_region,
    )


def s3_client_ctx() -> Any:
    s = get_settings()
    return make_session().client("s3", endpoint_url=s.s3_endpoint)


async def ensure_bucket(name: str) -> None:
    async with s3_client_ctx() as client:
        try:
            await client.head_bucket(Bucket=name)
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in ("404", "NoSuchBucket", "NotFound"):
                # If we can't even list, attempt create anyway; if that fails,
                # surface the error.
                pass
        try:
            await client.create_bucket(Bucket=name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                return
            raise
