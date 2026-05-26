"""Upload submission assets to Google Drive as a permanent archive.

Service account JSON in env GOOGLE_DRIVE_SA_JSON; root folder ID in GOOGLE_DRIVE_FOLDER_ID.
Layout in Drive:  <root>/<YYYY-MM>/<id>_<buyer>_<offer>/<file1>, <file2>, ...
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _client():
    sa_json = os.environ.get("GOOGLE_DRIVE_SA_JSON")
    if not sa_json:
        return None
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_or_create_folder(svc, name: str, parent_id: str) -> str:
    """Find a folder by name under parent, or create it. Returns folder id.

    NOTE: scope drive.file means we can only see folders we created — so list()
    will find folders created by *this* service account on prior runs, which is
    exactly what we want.
    """
    safe_name = name.replace("'", "\\'")
    q = (
        f"name = '{safe_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents "
        f"and trashed = false"
    )
    r = svc.files().list(q=q, spaces="drive", fields="files(id, name)", pageSize=10).execute()
    files = r.get("files", [])
    if files:
        return files[0]["id"]
    md = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    new = svc.files().create(body=md, fields="id").execute()
    return new["id"]


def _sanitize(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:max_len] or "unnamed"


def upload_submission_assets(
    files: list[Path],
    submission_id: str,
    offer: str,
    buyer_username: str | None,
    metadata: dict,
) -> str | None:
    """Upload all files plus a meta.json to Drive. Returns the submission folder URL or None."""
    svc = _client()
    if svc is None:
        print("  drive: GOOGLE_DRIVE_SA_JSON not set, skipping archive")
        return None

    root_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not root_id:
        print("  drive: GOOGLE_DRIVE_FOLDER_ID not set, skipping archive")
        return None

    month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    month_id = _find_or_create_folder(svc, month, root_id)

    who = buyer_username or "unknown"
    folder_name = f"{submission_id}_{_sanitize(who, 30)}_{_sanitize(offer, 40)}"
    sub_id = _find_or_create_folder(svc, folder_name, month_id)

    # Upload each file
    for p in files:
        if not p.exists() or not p.is_file():
            continue
        media = MediaFileUpload(str(p), resumable=False)
        svc.files().create(
            body={"name": p.name, "parents": [sub_id]},
            media_body=media,
            fields="id",
        ).execute()

    # Upload meta.json with submission text fields
    import tempfile, json as _json
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        _json.dump(metadata, f, ensure_ascii=False, indent=2)
        meta_path = f.name
    svc.files().create(
        body={"name": "meta.json", "parents": [sub_id]},
        media_body=MediaFileUpload(meta_path, mimetype="application/json"),
        fields="id",
    ).execute()

    return f"https://drive.google.com/drive/folders/{sub_id}"
