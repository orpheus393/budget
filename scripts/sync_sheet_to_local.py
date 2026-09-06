"""Google Sheets 거래내역 → 로컬 SQLite 회수 동기화.

로컬 모드로 옮긴 뒤에도 클라우드 cron이 계속 돌면, 클라우드가 먼저 처리한
메일은 구글 시트에만 저장되고 메일은 '처리완료'로 이동해 로컬 수집이
영원히 못 본다. 이 스크립트는 시트에만 있고 로컬 DB에 없는 거래를 찾아
로컬 DB로 가져온다 (반대 방향으로는 쓰지 않음 — 시트는 읽기 전용).

    python scripts/sync_sheet_to_local.py                # 전체 대조
    python scripts/sync_sheet_to_local.py 2026-07-29     # 그 날짜 이후만
    python scripts/sync_sheet_to_local.py 2026-07-29 --dry-run

중복 판정 키는 email_parser.save_to_sheets와 동일한 '날짜_출처_금액_내역'.
출력에는 건수만 찍고 가맹점·금액은 찍지 않는다.
"""

import os
import sys
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from localdb import open_workbook  # noqa: E402

SHEET_COLS = ["날짜", "시간", "출처", "유형", "금액", "내역",
              "카테고리", "원문", "잔액", "입력경로"]


def row_key(row: list) -> str:
    """save_to_sheets와 같은 중복 키: 날짜_출처_금액_내역."""
    cells = list(row) + [""] * (6 - len(row))
    return f"{cells[0]}_{cells[2]}_{cells[4]}_{cells[5]}"


def normalize(row: list) -> list:
    """10열로 맞추고, 입력경로가 비어 있으면 출처 기반으로 채운다."""
    cells = [str(c) if c is not None else "" for c in row]
    cells += [""] * (len(SHEET_COLS) - len(cells))
    cells = cells[:len(SHEET_COLS)]
    if not cells[9]:
        cells[9] = f"자동:{cells[2]}" if cells[2] else "불명"
    return cells


def missing_rows(sheet_rows: list, local_rows: list, since: str = "") -> list:
    """시트에만 있고 로컬에 없는 행 (헤더 제외, since 이후)."""
    local_keys = {row_key(r) for r in local_rows[1:]} if local_rows else set()
    out = []
    for row in (sheet_rows[1:] if sheet_rows else []):
        if not row or not row[0]:
            continue
        if since and row[0] < since:
            continue
        key = row_key(row)
        if key in local_keys:
            continue
        local_keys.add(key)          # 시트 안 중복도 한 번만
        out.append(normalize(row))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    since = args[0] if args else ""

    secrets_path = os.path.join(REPO_ROOT, ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_path):
        print(f"❌ {secrets_path} 없음 — secrets.toml.example 참고")
        sys.exit(1)
    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    sheet_id = secrets.get("GOOGLE_SHEET_ID", "")
    creds_dict = secrets.get("gcp_service_account")
    if not sheet_id or not creds_dict:
        print("❌ secrets.toml에 GOOGLE_SHEET_ID / [gcp_service_account] 필요\n"
              "   (회수가 끝나면 이 값들은 지워도 됩니다)")
        sys.exit(1)

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://spreadsheets.google.com/feeds",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(dict(creds_dict), scopes=scopes)
    src = gspread.authorize(creds).open_by_key(sheet_id)
    sheet_rows = src.worksheet("거래내역").get_all_values()

    db_path = str(secrets.get("DB_PATH", os.path.join(REPO_ROOT, "data", "budget.db")))
    dst = open_workbook(db_path)
    try:
        ws = dst.worksheet("거래내역")
    except gspread.WorksheetNotFound:
        ws = dst.add_worksheet("거래내역", rows=10000, cols=12)
        ws.append_row(SHEET_COLS)
    local_rows = ws.get_all_values()

    print(f"📥 시트: {len(sheet_rows) - 1}행 / 💾 로컬: {len(local_rows) - 1}행"
          f" (since={since or '전체'})")

    rows = missing_rows(sheet_rows, local_rows, since)
    if not rows:
        print("✅ 로컬에 없는 거래 없음 — 이미 동기화 상태")
        return

    by_month = {}
    for r in rows:
        by_month[r[0][:7]] = by_month.get(r[0][:7], 0) + 1
    for month in sorted(by_month):
        print(f"   · {month}: {by_month[month]}건")

    if dry_run:
        print(f"🔎 dry-run — {len(rows)}건 추가 예정 (실제 기록 안 함)")
        return

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"✅ {len(rows)}건 로컬 DB로 회수 완료 → {db_path}")


if __name__ == "__main__":
    main()
