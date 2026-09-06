"""Sync the Drive archive index (заявка → папка с крео) → Cloudflare KV.

Крео живут в R2 только 3 дня, а в Google Drive лежат вечно: `<YYYY-MM>/<СТАТУС>_REQ-…/`.
Скрипт проходит архив и складывает карту `REQ-<id> → folder_id` в KV-ключ `drive_index`,
чтобы кабинет показывал ссылку на крео у заявок, у которых видео уже удалено из R2.

Одна запись в KV на весь индекс — по ключу на заявку не разложить, у free-тира
лимит 1000 записей в сутки, а заявок тысячи.
"""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.request

from google.oauth2 import service_account
from googleapiclient.discovery import build


SUB_ID_RE = re.compile(r"REQ-\d{6}-\d{3}")
FOLDER_MIME = "application/vnd.google-apps.folder"


def list_folders(svc, parent_id: str, drive_id: str) -> list[dict]:
    """Все подпапки родителя (с пагинацией)."""
    out: list[dict] = []
    token = None
    while True:
        r = svc.files().list(
            q=f"'{parent_id}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(id, name)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="drive",
            driveId=drive_id,
        ).execute()
        out += r.get("files", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def main() -> None:
    sa = json.loads(os.environ["GOOGLE_DRIVE_SA_JSON"])
    print("service account:", sa.get("client_email"))
    creds = service_account.Credentials.from_service_account_info(
        sa, scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    root = os.environ["GOOGLE_DRIVE_FOLDER_ID"]
    months = sorted(list_folders(svc, root, root), key=lambda f: f["name"])
    print(f"месяцев в архиве: {len(months)}")

    index: dict[str, str] = {}
    dupes = 0
    for m in months:
        subs = list_folders(svc, m["id"], root)
        hit = 0
        for f in subs:
            match = SUB_ID_RE.search(f["name"])
            if not match:
                continue
            sid = match.group(0)
            # Заявку могли переразобрать и создать папку с другим статусом:
            # берём последнюю по порядку обхода (месяцы идут по возрастанию).
            if sid in index:
                dupes += 1
            index[sid] = f["id"]
            hit += 1
        print(f"  {m['name']}: папок {len(subs)}, заявок {hit}")
    print(f"итого заявок в индексе: {len(index)} (перезаписей: {dupes})")
    if not index:
        sys.exit("индекс пустой — проверь доступ сервис-аккаунта к шаред-драйву")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}"
        f"/storage/kv/namespaces/{os.environ['CF_KV_NAMESPACE']}/values/drive_index"
    )
    req = urllib.request.Request(
        url, data=json.dumps(index, ensure_ascii=False).encode(), method="PUT",
        headers={"Authorization": f"Bearer {os.environ['CF_API_TOKEN']}", "Content-Type": "text/plain"},
    )
    resp = json.loads(urllib.request.urlopen(req).read().decode())
    print("KV write success:", resp.get("success"))
    if not resp.get("success"):
        sys.exit(f"KV write failed: {resp}")


if __name__ == "__main__":
    main()
