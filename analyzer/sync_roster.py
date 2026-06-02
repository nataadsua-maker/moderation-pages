"""Sync the moderation-service buyer roster from the team Google Sheet → Cloudflare KV.

Reads the «Сотрудники» tab (by gid) of «Команда_Nataliia», builds a map
telegram_handle_lower → {trackerNick, fio}, and PUTs it to KV key `roster`.
Runs on a schedule from GitHub Actions (see .github/workflows/sync-roster.yml).
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

from google.oauth2 import service_account
from googleapiclient.discovery import build


def main() -> None:
    sa = json.loads(os.environ["GOOGLE_DRIVE_SA_JSON"])
    print("service account:", sa.get("client_email"))  # share the sheet with this email (Viewer)
    creds = service_account.Credentials.from_service_account_info(
        sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    sid = os.environ["TEAM_SHEET_ID"]
    gid = int(os.environ["TEAM_SHEET_GID"])

    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    title = None
    for sh in meta.get("sheets", []):
        if sh["properties"]["sheetId"] == gid:
            title = sh["properties"]["title"]
            break
    if not title:
        sys.exit(f"tab with gid={gid} not found")
    print("tab:", title)

    rng = f"'{title}'!A1:Z1000"
    vals = svc.spreadsheets().values().get(spreadsheetId=sid, range=rng).execute().get("values", [])
    if not vals:
        sys.exit("empty sheet")

    hdr = [str(h or "") for h in vals[0]]

    def col(substr: str) -> int:
        for i, h in enumerate(hdr):
            if substr.lower() in h.lower():
                return i
        return -1

    ci_fio = col("ФИО")
    ci_nick = col("Ник в трекере")
    ci_tg = col("Телега")
    ci_status = col("Стату")  # «Статуc» (Работает / Уволен)
    if min(ci_fio, ci_nick, ci_tg) < 0:
        sys.exit(f"required columns not found (ФИО={ci_fio} Ник={ci_nick} Телега={ci_tg})")

    def cell(row: list, i: int) -> str:
        return str(row[i]).strip() if 0 <= i < len(row) else ""

    roster: dict[str, dict] = {}
    fired = 0
    for row in vals[1:]:
        tg = cell(row, ci_tg)
        if not (tg.startswith("@") and len(tg) > 1):
            continue
        # Skip dismissed employees (Статус содержит «уволен»).
        if "увол" in cell(row, ci_status).lower():
            fired += 1
            continue
        key = tg.lower().lstrip("@").strip()
        if key:
            roster[key] = {"trackerNick": cell(row, ci_nick) or None, "fio": cell(row, ci_fio) or None}
    print(f"roster entries: {len(roster)} (skipped fired: {fired})")

    acc = os.environ["CF_ACCOUNT_ID"]
    tok = os.environ["CF_API_TOKEN"]
    ns = os.environ["CF_KV_NAMESPACE"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/storage/kv/namespaces/{ns}/values/roster"
    req = urllib.request.Request(
        url, data=json.dumps(roster, ensure_ascii=False).encode(), method="PUT",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/plain"},
    )
    resp = json.loads(urllib.request.urlopen(req).read().decode())
    print("KV write success:", resp.get("success"))
    if not resp.get("success"):
        sys.exit(f"KV write failed: {resp}")


if __name__ == "__main__":
    main()
