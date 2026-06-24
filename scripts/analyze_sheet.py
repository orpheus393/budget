"""일회용: Google Sheet 거래내역 요약 분석.

거래 내역을 카테고리별·월별·출처별로 집계해 가계 패턴 파악.
환경변수: GOOGLE_SHEET_ID, GOOGLE_CREDS_JSON (기존 워크플로와 동일).
출력은 stdout에 markdown 형태로.
"""

import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials


NON_PNL = {"어머니차입금", "부채청산", "자기이체", "환불/캐시백", "수입"}
FIXED = {"주거/관리", "주거/대출", "통신", "구독", "보험/금융", "교육/자녀"}


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


def main():
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

    ws = gc.open_by_key(sheet_id).worksheet("거래내역")
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("# 시트 비어있음")
        return
    header = rows[0]
    print(f"# 가계부 요약 분석 (총 {len(rows) - 1}개 거래)\n")

    # (year, month) → 카테고리 → 금액 누적 (출금 +, 입금 -)
    monthly_cat = defaultdict(lambda: defaultdict(int))
    # (year, month) → 출처 → 금액
    monthly_src = defaultdict(lambda: defaultdict(int))
    # 가맹점 누적 (전체 기간)
    merchant_total = defaultdict(int)
    # 월별 입금/출금 합계
    monthly_io = defaultdict(lambda: {"입금": 0, "출금": 0, "고정비": 0, "변동비": 0})
    # 카테고리 통계
    cat_counts = defaultdict(int)
    # 무라벨 (카테고리=기타) 큰 금액
    unlabeled_big = []
    # 이상치: 단건 30만 이상 출금
    big_singles = []
    # 카드비 (각 카드사 자동이체)
    card_bills = defaultdict(list)  # 출처 → [(date, amount, 내역)]
    # 사용자 지정 날짜 범위 내 전체 거래 (제주여행 등 점검용)
    date_range_rows = []
    range_from = os.environ.get("DATE_FROM", "").strip()
    range_to = os.environ.get("DATE_TO", "").strip()
    dr_from = parse_date(range_from) if range_from else None
    dr_to = parse_date(range_to) if range_to else None

    all_dates = []

    for row in rows[1:]:
        if len(row) < 7:
            continue
        date_s, time_s, src, typ, amt_s, mer, cat = row[:7]
        amt = parse_amount(amt_s)
        if amt <= 0:
            continue
        d = parse_date(date_s)
        if not d:
            continue
        all_dates.append(d)
        ym = (d.year, d.month)
        cat_counts[cat] += 1

        # 사용자 지정 범위 점검
        if dr_from and dr_to and dr_from <= d <= dr_to:
            date_range_rows.append((d, src, typ, amt, mer, cat))

        # 손익 외 제외
        if cat in NON_PNL or typ == "입금":
            monthly_src[ym][src] += -amt if typ == "입금" else amt
            if typ == "입금":
                monthly_io[ym]["입금"] += amt
            continue

        # 출금만 카테고리 집계
        monthly_cat[ym][cat] += amt
        monthly_src[ym][src] += amt
        monthly_io[ym]["출금"] += amt
        if cat in FIXED:
            monthly_io[ym]["고정비"] += amt
        else:
            monthly_io[ym]["변동비"] += amt

        if mer:
            merchant_total[mer] += amt

        # 미분류 큰 금액
        if cat == "기타" and amt >= 50000:
            unlabeled_big.append((d, amt, mer, src))

        # 단건 30만+
        if amt >= 300000:
            big_singles.append((d, amt, mer, cat, src))

        # 카드비 (카드 결제 자동이체)
        if "카드" in mer or "결제" in mer:
            if src == "IBK기업은행" or src == "카카오뱅크":
                card_bills[src].append((d, amt, mer))

    if not all_dates:
        print("거래 0건")
        return

    min_d, max_d = min(all_dates), max(all_dates)
    print(f"기간: **{min_d}** ~ **{max_d}**\n")

    months = sorted(monthly_cat.keys())[-4:]  # 최근 4개월

    # 1) 월별 손익
    print("## 월별 손익 (최근 4개월)")
    print("| 월 | 수입(입금합) | 지출 | 손익 | 고정비 | 변동비 |")
    print("|---|---:|---:|---:|---:|---:|")
    for ym in months:
        io = monthly_io[ym]
        net = io["입금"] - io["출금"]
        print(f"| {ym[0]}-{ym[1]:02d} | {io['입금']:,} | {io['출금']:,} | {net:+,} | {io['고정비']:,} | {io['변동비']:,} |")
    print()

    # 2) 카테고리별 (최근 월 vs 직전 월)
    if len(months) >= 2:
        cur, prev = months[-1], months[-2]
        print(f"## 카테고리별 지출: {cur[0]}-{cur[1]:02d} vs {prev[0]}-{prev[1]:02d}")
        print("| 카테고리 | 이번 달 | 직전 달 | 변동 |")
        print("|---|---:|---:|---:|")
        all_cats = set(monthly_cat[cur].keys()) | set(monthly_cat[prev].keys())
        for cat in sorted(all_cats, key=lambda c: -monthly_cat[cur].get(c, 0)):
            a = monthly_cat[cur].get(cat, 0)
            b = monthly_cat[prev].get(cat, 0)
            diff = a - b
            sign = "📈" if diff > 50000 else ("📉" if diff < -50000 else "  ")
            print(f"| {cat} | {a:,} | {b:,} | {sign} {diff:+,} |")
        print()

    # 3) 최근 월 출처별
    last = months[-1]
    print(f"## 최근 월 출처별 ({last[0]}-{last[1]:02d})")
    print("| 출처 | 금액 |")
    print("|---|---:|")
    for src, amt in sorted(monthly_src[last].items(), key=lambda x: -x[1]):
        if amt > 0:
            print(f"| {src} | {amt:,} |")
    print()

    # 4) TOP 가맹점 (전체 기간)
    print("## TOP 10 가맹점 (전체 기간 누계)")
    print("| 가맹점 | 누계 |")
    print("|---|---:|")
    for mer, amt in sorted(merchant_total.items(), key=lambda x: -x[1])[:10]:
        print(f"| {mer[:40]} | {amt:,} |")
    print()

    # 5) 단건 30만+ (최근 3개월)
    cutoff_ym = months[-3] if len(months) >= 3 else months[0]
    big_recent = [b for b in big_singles if (b[0].year, b[0].month) >= cutoff_ym]
    big_recent.sort(key=lambda x: -x[1])
    if big_recent:
        print(f"## 단건 30만원+ 거래 (최근 3개월, 상위 15개)")
        print("| 날짜 | 금액 | 카테고리 | 가맹점 | 출처 |")
        print("|---|---:|---|---|---|")
        for d, amt, mer, cat, src in big_recent[:15]:
            print(f"| {d} | {amt:,} | {cat} | {mer[:30]} | {src} |")
        print()

    # 6) 미분류 "기타" 큰 금액 (재분류 후보)
    unlabeled_recent = [u for u in unlabeled_big if (u[0].year, u[0].month) >= cutoff_ym]
    unlabeled_recent.sort(key=lambda x: -x[1])
    if unlabeled_recent:
        print(f"## '기타' 분류된 5만원+ 거래 (최근 3개월, 카테고리 매핑 누락 후보) — 상위 15")
        print("| 날짜 | 금액 | 가맹점 | 출처 |")
        print("|---|---:|---|---|")
        for d, amt, mer, src in unlabeled_recent[:15]:
            print(f"| {d} | {amt:,} | {mer[:30]} | {src} |")
        print()

    # 7) 식비 추세 (최근 6개월)
    print("## 식비 추세 (최근 6개월)")
    food_months = sorted(monthly_cat.keys())[-6:]
    print("| 월 | 식비 |")
    print("|---|---:|")
    for ym in food_months:
        f = monthly_cat[ym].get("식비", 0)
        print(f"| {ym[0]}-{ym[1]:02d} | {f:,} |")
    print()

    # 8) 진단 (자동 발견)
    print("## 🔍 자동 진단")
    last_cats = monthly_cat[last]
    prev_cats = monthly_cat[months[-2]] if len(months) >= 2 else {}
    last_total_out = monthly_io[last]["출금"]
    findings = []
    for cat, amt in last_cats.items():
        if last_total_out > 0:
            pct = amt / last_total_out * 100
            if pct >= 25:
                findings.append((amt, f"⚠️ **{cat}**가 이번 달 지출의 **{pct:.0f}%** ({amt:,}원) — 단일 카테고리 비중 과대"))
        prev_amt = prev_cats.get(cat, 0)
        if prev_amt > 0 and amt > prev_amt * 1.5 and amt - prev_amt >= 100000:
            findings.append((amt - prev_amt, f"📈 **{cat}**가 전월 대비 {(amt/prev_amt - 1) * 100:.0f}% 증가 ({prev_amt:,} → {amt:,})"))
    # 고정비 비중
    if last_total_out > 0:
        fixed_pct = monthly_io[last]["고정비"] / last_total_out * 100
        if fixed_pct >= 50:
            findings.append((monthly_io[last]["고정비"], f"⚠️ **고정비가 지출의 {fixed_pct:.0f}%** — 고정 지출 부담 큼"))
    findings.sort(key=lambda x: -x[0])
    if findings:
        for _, msg in findings[:5]:
            print(f"- {msg}")
    else:
        print("- 특이사항 없음")

    # 9) 사용자 지정 날짜 범위 전체 거래 (DATE_FROM~DATE_TO 환경변수)
    if dr_from and dr_to and date_range_rows:
        print(f"\n## 📅 지정 기간 ({dr_from} ~ {dr_to}) 전체 거래 ({len(date_range_rows)}건)")
        print("| 날짜 | 출처 | 유형 | 금액 | 가맹점 | 카테고리 |")
        print("|---|---|---|---:|---|---|")
        date_range_rows.sort(key=lambda r: (r[0], r[1]))
        out_sum = sum(r[3] for r in date_range_rows if r[2] == "출금")
        in_sum = sum(r[3] for r in date_range_rows if r[2] == "입금")
        for d, src, typ, amt, mer, cat in date_range_rows:
            print(f"| {d} | {src} | {typ} | {amt:,} | {mer[:30]} | {cat} |")
        print(f"\n**기간 합계**: 출금 {out_sum:,} / 입금 {in_sum:,} / 순 {out_sum - in_sum:+,}")


if __name__ == "__main__":
    main()
