"""Upload submission assets to Google Drive (Shared Drive) as a permanent archive.

Service account JSON in env GOOGLE_DRIVE_SA_JSON; shared drive ID in GOOGLE_DRIVE_FOLDER_ID.

Layout in the shared drive:
    <root>/<YYYY-MM>/<STATUS>_<id>_<buyer>_<offer>/
        0.mp4
        1.mp4
        meta.json
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


def _find_or_create_folder(svc, name: str, parent_id: str, drive_id: str) -> str:
    """Find or create a folder by name under parent inside the shared drive."""
    safe_name = name.replace("'", "\\'")
    q = (
        f"name = '{safe_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents "
        f"and trashed = false"
    )
    r = svc.files().list(
        q=q,
        spaces="drive",
        fields="files(id, name)",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="drive",
        driveId=drive_id,
    ).execute()
    files = r.get("files", [])
    if files:
        return files[0]["id"]
    md = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    new = svc.files().create(body=md, fields="id", supportsAllDrives=True).execute()
    return new["id"]


def _sanitize(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\- ]", "", s or "", flags=re.UNICODE).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:max_len] or "unnamed"


def _status_label(verdict: dict) -> str:
    """APPROVED / REJECTED / MANUAL_REVIEW based on the verdict shape."""
    if verdict.get("overall") == "approve":
        return "APPROVED"
    violations = verdict.get("violations") or []
    if violations and all(v.get("policy_section") == "manual_review" for v in violations):
        return "MANUAL_REVIEW"
    return "REJECTED"


def upload_submission_assets(
    files: list[Path],
    submission_id: str,
    offer: str,
    buyer_username: str | None,
    metadata: dict,
) -> str | None:
    """Upload assets + meta.json. Returns submission folder URL or None."""
    svc = _client()
    if svc is None:
        print("  drive: GOOGLE_DRIVE_SA_JSON not set, skipping archive")
        return None

    drive_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not drive_id:
        print("  drive: GOOGLE_DRIVE_FOLDER_ID not set, skipping archive")
        return None

    month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    month_id = _find_or_create_folder(svc, month, drive_id, drive_id)

    status = _status_label(metadata.get("verdict") or {})
    who = buyer_username or "unknown"
    folder_name = f"{status}_{submission_id}_{_sanitize(who, 30)}_{_sanitize(offer, 40)}"
    sub_folder_id = _find_or_create_folder(svc, folder_name, month_id, drive_id)

    # Upload each asset file
    for p in files:
        if not p.exists() or not p.is_file():
            continue
        media = MediaFileUpload(str(p), resumable=False)
        svc.files().create(
            body={"name": p.name, "parents": [sub_folder_id]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()

    # Upload meta.json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
        meta_path = f.name
    svc.files().create(
        body={"name": "meta.json", "parents": [sub_folder_id]},
        media_body=MediaFileUpload(meta_path, mimetype="application/json"),
        fields="id",
        supportsAllDrives=True,
    ).execute()

    return f"https://drive.google.com/drive/folders/{sub_folder_id}"
