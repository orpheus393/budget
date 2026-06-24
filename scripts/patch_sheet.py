"""시트 패치: 카테고리 재분류 + BC체크/IBK 중복 행 제거.

두 가지 작업 모드(env OPERATION):

1) OPERATION=recategorize
   주어진 조건(날짜·출처·가맹점)에 매칭되는 행의 '카테고리'를 NEW_CATEGORY로 교체.
   - DATE_FROM / DATE_TO: YYYY-MM-DD (선택)
   - SOURCE_FILTER: 출처 부분 일치 (선택)
   - MERCHANT_CONTAINS: 가맹점 부분 일치 (선택)
   - SKIP_CATEGORIES: 건너뛸 카테고리 csv (예: "주거/대출,부채청산,어머니차입금,자기이체,개인송금,수입,환불/캐시백")
   - SKIP_INCOMING: "true"면 유형=입금 행 건너뜀 (기본 true)
   - NEW_CATEGORY: 새 카테고리명 (필수)
   - DRY_RUN: "true"면 미리보기만 (기본 true)

2) OPERATION=dedup_ibk_bc
   같은 날짜·같은 금액에 BC카드(체크) 행과 IBK기업은행 행이 둘 다 있으면
   IBK 행을 삭제 (BC체크가 더 정확한 가맹점명 보유).
   - DRY_RUN 동일

환경변수: GOOGLE_SHEET_ID, GOOGLE_CREDS_JSON 필수.
"""

import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials


def parse_amount(s):
    try:
        return int(str(s).replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def open_sheet():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not sheet_id or not creds_json:
        print("환경변수 GOOGLE_SHEET_ID / GOOGLE_CREDS_JSON 필요", file=sys.stderr)
        sys.exit(1)
    creds_dict = json.loads(creds_json)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(creds_dict, f)
        creds_path = f.name
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    gc = gspread.authorize(Credentials.from_service_account_file(creds_path, scopes=scopes))
    os.unlink(creds_path)
    return gc.open_by_key(sheet_id).worksheet("거래내역")


def op_recategorize(ws, dry_run):
    df = os.environ.get("DATE_FROM", "").strip()
    dt = os.environ.get("DATE_TO", "").strip()
    src_f = os.environ.get("SOURCE_FILTER", "").strip()
    mer_f = os.environ.get("MERCHANT_CONTAINS", "").strip()
    skip_cats = {c.strip() for c in os.environ.get("SKIP_CATEGORIES", "").split(",") if c.strip()}
    skip_incoming = os.environ.get("SKIP_INCOMING", "true").lower() != "false"
    new_cat = os.environ.get("NEW_CATEGORY", "").strip()
    if not new_cat:
        print("NEW_CATEGORY 필수", file=sys.stderr)
        sys.exit(1)
    df_d = parse_date(df) if df else None
    dt_d = parse_date(dt) if dt else None

    rows = ws.get_all_values()
    if not rows:
        print("시트 비어있음")
        return
    targets = []  # (row_num, current_cat, mer, date, amt, src)
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 7:
            continue
        date_s, _time, src, typ, amt_s, mer, cat = row[:7]
        amt = parse_amount(amt_s)
        d = parse_date(date_s)
        if df_d and (not d or d < df_d):
            continue
        if dt_d and (not d or d > dt_d):
            continue
        if src_f and src_f not in src:
            continue
        if mer_f and mer_f not in mer:
            continue
        if cat in skip_cats:
            continue
        if skip_incoming and typ == "입금":
            continue
        if cat == new_cat:
            continue  # already correct
        targets.append((i, cat, mer, d, amt, src, typ))

    print(f"매칭된 행: 총 {len(targets)}개  → 카테고리 '{new_cat}'으로 변경 예정")
    print(f"필터: DATE={df}~{dt}, SOURCE='{src_f}', MERCHANT='{mer_f}', SKIP_CATS={skip_cats}, SKIP_INCOMING={skip_incoming}")
    print("-" * 90)
    for tgt in targets[:30]:
        i, cat, mer, d, amt, src, typ = tgt
        print(f"  row {i}: {d} | {src} | {typ} | {amt:,} | {mer[:30]} | {cat} → {new_cat}")
    if len(targets) > 30:
        print(f"  ... 외 {len(targets) - 30}개")

    if dry_run:
        print("\nDRY_RUN — 실제 수정 안 함. DRY_RUN=false 로 재실행.")
        return
    if not targets:
        return

    # 카테고리 컬럼은 G(7번째). 일괄 업데이트.
    updates = [{"range": f"G{i}", "values": [[new_cat]]} for (i, *_) in targets]
    ws.batch_update(updates, value_input_option="RAW")
    print(f"\n{len(targets)}행 업데이트 완료")


def op_dedup_ibk_bc(ws, dry_run):
    """같은 날짜·같은 금액의 BC카드(체크) + IBK기업은행 쌍을 찾아 IBK 행 삭제.

    체크카드 사용 시 두 군데 (BC체크 명세서 + IBK 통장 출금) 모두 기록되어
    이중계상 발생. BC체크 쪽이 가맹점명 더 정확하므로 IBK 쪽을 제거.
    """
    rows = ws.get_all_values()
    if not rows:
        print("시트 비어있음")
        return
    # (date, amount) → list of (row_num, src, mer, typ)
    by_key = defaultdict(list)
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 7:
            continue
        date_s, _t, src, typ, amt_s, mer, _cat = row[:7]
        amt = parse_amount(amt_s)
        d = parse_date(date_s)
        if not d or amt <= 0 or typ != "출금":
            continue
        by_key[(d, amt)].append((i, src, mer))

    to_delete = []  # row numbers
    pairs = []  # for preview
    for key, lst in by_key.items():
        bc = [x for x in lst if x[1] == "BC카드(체크)"]
        ibk = [x for x in lst if x[1] == "IBK기업은행"]
        if not bc or not ibk:
            continue
        # 각 BC체크 행마다 매칭되지 않은 IBK 행 1개를 짝짓고 IBK 쪽 삭제
        used_ibk = set()
        for b in bc:
            for j, ix in enumerate(ibk):
                if j in used_ibk:
                    continue
                used_ibk.add(j)
                pairs.append((key[0], key[1], b, ix))
                to_delete.append(ix[0])
                break

    print(f"매칭된 BC체크 ↔ IBK 중복 쌍: {len(pairs)}개  → IBK 행 {len(to_delete)}개 삭제 예정")
    print("-" * 90)
    for d, amt, b, ix in pairs[:30]:
        print(f"  {d} {amt:,}원  BC체크[row {b[0]}] '{b[2][:25]}' ↔ IBK[row {ix[0]}] '{ix[2][:25]}'")
    if len(pairs) > 30:
        print(f"  ... 외 {len(pairs) - 30}개")

    if dry_run:
        print("\nDRY_RUN — 실제 삭제 안 함. DRY_RUN=false 로 재실행.")
        return
    if not to_delete:
        return
    # 뒤에서부터 삭제 (인덱스 변동 방지)
    for row_num in sorted(set(to_delete), reverse=True):
        ws.delete_rows(row_num)
    print(f"\n{len(to_delete)}행 삭제 완료")


def main():
    op = os.environ.get("OPERATION", "").strip().lower()
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    if op not in ("recategorize", "dedup_ibk_bc"):
        print(f"OPERATION 값 필요: 'recategorize' 또는 'dedup_ibk_bc'", file=sys.stderr)
        sys.exit(1)
    ws = open_sheet()
    if op == "recategorize":
        op_recategorize(ws, dry_run)
    else:
        op_dedup_ibk_bc(ws, dry_run)


if __name__ == "__main__":
    main()
