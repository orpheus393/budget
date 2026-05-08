"""
일회용: Google Sheet 거래내역에서 특정 출처(또는 카테고리/원문 키워드)의 행을 삭제.
주로 BC카드처럼 한 번 잘못 들어간 데이터를 한꺼번에 정리할 때 사용.

환경변수:
- GOOGLE_SHEET_ID, GOOGLE_CREDS_JSON: 기존 워크플로와 동일
- SOURCE_FILTER: 출처 (예: "BC카드"). 미지정 시 작업 안 함.
- ORIGIN_CONTAINS: 원문 컬럼에 이 문자열이 포함된 행만 (예: "월간명세서").
                   미지정 시 SOURCE_FILTER만 일치하면 삭제.
- DRY_RUN: "true"면 삭제하지 않고 어떤 행이 지워질지만 출력 (기본 true).
           실제 삭제하려면 "false"로 명시.
"""

import json
import os
import sys
import tempfile

import gspread
from google.oauth2.service_account import Credentials


def main():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not sheet_id or not creds_json:
        print("환경변수 GOOGLE_SHEET_ID / GOOGLE_CREDS_JSON 필요")
        sys.exit(1)

    source_filter = os.environ.get("SOURCE_FILTER", "").strip()
    origin_contains = os.environ.get("ORIGIN_CONTAINS", "").strip()
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"

    if not source_filter and not origin_contains:
        print("SOURCE_FILTER 또는 ORIGIN_CONTAINS 둘 중 하나는 지정해야 합니다.")
        sys.exit(1)

    creds_dict = json.loads(creds_json)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(creds_dict, f)
        creds_path = f.name

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    gc = gspread.authorize(creds)
    os.unlink(creds_path)

    sheet = gc.open_by_key(sheet_id)
    ws = sheet.worksheet("거래내역")
    rows = ws.get_all_values()
    if not rows:
        print("시트가 비어있음")
        return

    header = rows[0]
    print(f"헤더: {header}")
    print(f"필터: source={source_filter or '(any)'}, origin_contains={origin_contains or '(any)'}")
    print(f"DRY_RUN={dry_run}")
    print("-" * 80)

    # 1-base 행 번호 (헤더 1행, 데이터 2행부터)
    rows_to_delete = []
    for i, row in enumerate(rows[1:], start=2):
        if not row:
            continue
        src = row[2] if len(row) > 2 else ""
        origin = row[7] if len(row) > 7 else ""
        if source_filter and src != source_filter:
            continue
        if origin_contains and origin_contains not in origin:
            continue
        rows_to_delete.append(i)
        # 미리보기 출력 (앞 5행만)
        if len(rows_to_delete) <= 5:
            print(f"  row {i}: {' | '.join(row)}")

    print(f"... 매칭된 행: 총 {len(rows_to_delete)}개")
    if not rows_to_delete:
        print("삭제할 행 없음")
        return

    if dry_run:
        print("\nDRY_RUN 모드 — 실제로 삭제 안 함. 삭제하려면 DRY_RUN=false 로 재실행.")
        return

    # 뒤에서부터 삭제 (인덱스 변동 방지)
    print(f"\n{len(rows_to_delete)}행 삭제 시작...")
    for row_num in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(row_num)
    print("삭제 완료")


if __name__ == "__main__":
    main()
