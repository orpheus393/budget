"""1회용: Google Sheets → 로컬 SQLite(data/budget.db) 이관.

PC에서 실행 (클라우드 데이터는 읽기만 하고 건드리지 않음 — 비파괴):

    python scripts/migrate_to_sqlite.py

자격증명은 .streamlit/secrets.toml에서 읽는다 (gcp_service_account,
GOOGLE_SHEET_ID). 이관 대상 워크시트: 거래내역, 예산, 보금자리론.
이미 로컬 DB에 같은 워크시트가 있으면 덮어쓰기 전에 확인을 묻는다.
"""

import json
import os
import sys
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from localdb import open_workbook  # noqa: E402

WORKSHEETS = ["거래내역", "예산", "보금자리론"]


def _load_secrets() -> dict:
    path = os.path.join(REPO_ROOT, ".streamlit", "secrets.toml")
    if not os.path.exists(path):
        print(f"❌ {path} 가 없습니다. secrets.toml.example을 참고해 만들어주세요.")
        sys.exit(1)
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    secrets = _load_secrets()
    sheet_id = secrets.get("GOOGLE_SHEET_ID", "")
    creds_dict = secrets.get("gcp_service_account")
    if not sheet_id or not creds_dict:
        print("❌ secrets.toml에 GOOGLE_SHEET_ID / [gcp_service_account] 필요")
        sys.exit(1)

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(dict(creds_dict), scopes=scopes)
    gc = gspread.authorize(creds)
    src = gc.open_by_key(sheet_id)
    print(f"📥 원본: {src.title} ({src.url})")

    db_path = str(secrets.get("DB_PATH", os.path.join(REPO_ROOT, "data", "budget.db")))
    dst = open_workbook(db_path)
    print(f"📤 대상: {dst.url}")

    for name in WORKSHEETS:
        try:
            ws_src = src.worksheet(name)
        except gspread.WorksheetNotFound:
            print(f"  · '{name}' — 원본에 없음, 건너뜀")
            continue
        rows = ws_src.get_all_values()

        try:
            existing = dst.worksheet(name).get_all_values()
        except gspread.WorksheetNotFound:
            existing = None
        if existing:
            ans = input(
                f"  ⚠️ 로컬 '{name}'에 이미 {len(existing)}행 존재. 덮어쓸까요? [y/N] "
            ).strip().lower()
            if ans != "y":
                print(f"  · '{name}' — 건너뜀")
                continue

        ws_dst = dst.add_worksheet(name)
        ws_dst.clear()
        if rows:
            ws_dst.append_rows(rows)
        print(f"  ✅ '{name}' — {len(rows)}행 이관 (헤더 포함)")

    print("\n완료! 다음 단계:")
    print("  1. secrets.toml에  STORAGE = \"sqlite\"  추가")
    print("  2. streamlit run app.py  → 로컬 DB로 대시보드 확인")
    print(f"  3. 백업: {db_path} 파일을 드라이브 동기화 폴더 등에 복사")


if __name__ == "__main__":
    main()
