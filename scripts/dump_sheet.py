"""
일회용: Google Sheet 거래내역의 특정 출처 행을 콘솔에 출력.
GitHub Actions 워크플로 'sheet 덤프' 단계에서 사용.

환경변수:
- GOOGLE_SHEET_ID, GOOGLE_CREDS_JSON: 기존 워크플로와 동일
- SOURCE_FILTER: 출처 (예: "BC카드"). 부분 일치 — "BC카드"로 검색하면
  "BC카드", "BC카드(신용)", "BC카드(체크)" 모두 매칭. 미지정 시 전체.
- LIMIT: 최대 출력 행 수 (기본 50)
- SUMMARY_ONLY: "1"이면 개별 행 대신 월×출처 집계만 출력.
  저장소가 public이면 Actions 로그도 공개되므로, 가맹점·금액이 드러나지
  않는 이 모드로 규모만 파악할 것.
- SINCE: "YYYY-MM-DD" 이후 행만 대상 (집계·출력 공통, 선택)
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
    summary_only = os.environ.get("SUMMARY_ONLY", "").strip() in ("1", "true", "yes")
    since = os.environ.get("SINCE", "").strip()
    try:
        limit = int(os.environ.get("LIMIT", "50"))
    except ValueError:
        limit = 50

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
        print("시트가 비어있습니다")
        return

    header = rows[0]

    if summary_only:
        # 월×출처 건수만 — 가맹점/금액 등 민감 정보는 출력하지 않는다.
        from collections import Counter
        counter = Counter()
        latest = ""
        total_match = 0
        for row in rows[1:]:
            date = row[0] if row else ""
            source = row[2] if len(row) > 2 else ""
            path = row[9] if len(row) > 9 else ""
            if source_filter and source_filter not in source:
                continue
            if since and date < since:
                continue
            total_match += 1
            latest = max(latest, date)
            counter[(date[:7], source, path)] += 1
        print(f"{'월':<9} {'출처':<14} {'입력경로':<16} 건수")
        print("-" * 52)
        for (month, source, path), n in sorted(counter.items()):
            print(f"{month:<9} {source:<14} {path:<16} {n}")
        print("-" * 52)
        print(f"합계 {total_match}행 (since={since or '(전체)'}, "
              f"source_filter={source_filter or '(전체)'}, 최신 날짜={latest or '-'})")
        return

    print(" | ".join(header))
    print("-" * 120)

    matched = 0
    total_match = 0
    for row in rows[1:]:
        if source_filter:
            if len(row) < 3 or source_filter not in row[2]:
                continue
        if since and (not row or row[0] < since):
            continue
        total_match += 1
        if matched < limit:
            print(" | ".join(row))
            matched += 1
            if matched == limit:
                print(f"... ({limit}행 출력 후 truncate, 매칭 행은 계속 셈)")

    print("-" * 120)
    print(f"매칭 행 {total_match}개 (출력 {matched}행, source_filter={source_filter or '(전체)'})")


if __name__ == "__main__":
    main()
