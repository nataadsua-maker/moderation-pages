"""Download video files from R2 directly using S3 credentials."""
from __future__ import annotations
import os
from pathlib import Path

import boto3


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_S3_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def download(key: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _client().download_file(os.environ["R2_BUCKET"], key, str(dest))
    return dest
