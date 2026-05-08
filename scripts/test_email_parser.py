"""
email_parser 단위 테스트.
GitHub Actions 워크플로 실행 시 자동 검증되지는 않지만,
로컬에서 `python scripts/test_email_parser.py`로 빠르게 회귀를 잡을 수 있다.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from email_parser import (
    parse_amount,
    parse_transaction_type,
    parse_merchant,
    guess_category,
    classify_non_transaction,
    strip_html,
    imap_utf7_encode,
    is_statement_email,
    is_non_transaction_subject,
    normalize_statement_date,
    normalize_statement_amount,
    parse_statement_table,
    parse_statement_text,
    _is_amount_token,
    AD_FOLDER,
    SHOPPING_FOLDER,
    NEWSLETTER_FOLDER,
    SNS_FOLDER,
)


def assert_eq(actual, expected, label):
    status = "✅" if actual == expected else "❌"
    print(f"{status} {label}: got={actual!r}  expected={expected!r}")
    if actual != expected:
        global FAILED
        FAILED += 1


FAILED = 0

# ── strip_html ──
assert_eq(
    strip_html("<p>안녕<br>하세요</p>&nbsp;끝"),
    "안녕\n하세요\n 끝",
    "strip_html basic",
)
assert_eq(
    strip_html("<style>x{}</style><div>안녕</div>"),
    "안녕",
    "strip_html drops style",
)

# ── parse_amount: 거래 키워드 근접 매치 ──
assert_eq(
    parse_amount("스타벅스에서 12,500원 결제하셨습니다"),
    12500,
    "amount near keyword (after)",
)
assert_eq(
    parse_amount("결제 12,500원 / 스타벅스"),
    12500,
    "amount near keyword (before)",
)
assert_eq(
    parse_amount("이용한도 5,000,000원, 결제 8,200원, 잔액 1,234원"),
    8200,
    "amount ignores 한도/잔액",
)
assert_eq(
    parse_amount("승인 50원 — 작은 금액 노이즈"),
    50,  # 승인 키워드와 함께면 100원 미만도 받아들임
    "amount with keyword keeps small value",
)
assert_eq(
    parse_amount("적립 30원 적립금 누적 9,999원"),
    None,
    "amount blacklist suppresses adds",
)

# ── parse_transaction_type ──
assert_eq(
    parse_transaction_type("결제 12,500원", source="나이스정보통신"),
    "출금",
    "PG default 출금",
)
assert_eq(
    parse_transaction_type("취소 12,500원", source="토스페이먼츠"),
    "입금",
    "PG cancel = 입금",
)
assert_eq(
    parse_transaction_type("12,500원이 입금되었습니다"),
    "입금",
    "kw 입금",
)

# ── parse_merchant ──
assert_eq(
    parse_merchant("님, 스타벅스코리아에서 결제한 내역입니다", "토스페이먼츠"),
    "스타벅스코리아",
    "merchant from 토스",
)
assert_eq(
    parse_merchant("가맹점: 메가커피 강남점", "BC카드"),
    "메가커피 강남점",
    "merchant from BC",
)

# ── guess_category ──
assert_eq(guess_category("스타벅스 강남점", "출금"), "식비", "category 식비")
assert_eq(guess_category("쿠팡", "출금"), "쇼핑", "category 쇼핑")
assert_eq(guess_category("KT 통신비", "출금"), "통신", "category 통신")
assert_eq(guess_category("급여", "입금"), "수입", "category 수입")

# ── classify_non_transaction ──
assert_eq(
    classify_non_transaction("noreply@coupang.com", "주문확인"),
    SHOPPING_FOLDER,
    "classify shopping by sender",
)
assert_eq(
    classify_non_transaction("notify@instagram.com", "DM 받음"),
    SNS_FOLDER,
    "classify SNS",
)
assert_eq(
    classify_non_transaction("hello@news.example.com", "뉴스레터 #42"),
    NEWSLETTER_FOLDER,
    "classify newsletter",
)
assert_eq(
    classify_non_transaction("event@brand.com", "(광고) 30% 할인 쿠폰"),
    AD_FOLDER,
    "classify ad by subject",
)
assert_eq(
    classify_non_transaction("friend@example.com", "안녕"),
    None,
    "no match",
)

# ── imap_utf7_encode ──
assert_eq(imap_utf7_encode("INBOX"), "INBOX", "utf7 ascii")
assert_eq(imap_utf7_encode("&"), "&-", "utf7 ampersand")
# 한글 폴더는 디코딩 검증으로 확인
encoded = imap_utf7_encode("가계부_처리완료")
print(f"   가계부_처리완료 → {encoded}")
# round-trip via Python's imap4-utf-7 if possible
try:
    decoded = encoded.encode("ascii").decode("imap4-utf-7")
    assert_eq(decoded, "가계부_처리완료", "utf7 roundtrip")
except (LookupError, UnicodeDecodeError):
    print("   imap4-utf-7 codec not available — skipping roundtrip")

# ── BC카드 명세서 ──
assert_eq(is_statement_email("임영재님의 IBK기업은행 BC카드 2026년 04월 19일 이용대금명세서입니다."),
          True, "is_statement_email true")
assert_eq(is_statement_email("BC카드 12,500원 결제"), False, "is_statement_email false")

# 날짜 정규화: 명세서 발행이 2026-04, 거래월이 03이면 같은 해
assert_eq(normalize_statement_date("03/15", 2026, 4), "2026-03-15", "stmt date 03/15")
assert_eq(normalize_statement_date("4.10", 2026, 4), "2026-04-10", "stmt date 4.10")
# 1월 명세에서 12월 거래는 전년도
assert_eq(normalize_statement_date("12-28", 2026, 1), "2025-12-28", "stmt date prev year")
# 풀 날짜
assert_eq(normalize_statement_date("2026.04.05", 2026, 4), "2026-04-05", "stmt date full")
assert_eq(normalize_statement_date("aaa", 2026, 4), None, "stmt date invalid")

# 금액 정규화
assert_eq(normalize_statement_amount("12,500원"), 12500, "stmt amount 콤마+원")
assert_eq(normalize_statement_amount("-3,000"), -3000, "stmt amount 음수")
assert_eq(normalize_statement_amount("(1,500)"), -1500, "stmt amount 괄호")
assert_eq(normalize_statement_amount(""), None, "stmt amount empty")

# 표 파싱
sample_table = [
    ["이용일", "가맹점명", "이용금액"],
    ["03/12", "스타벅스 강남점", "5,800"],
    ["03/15", "쿠팡", "23,400원"],
    ["", "", ""],  # 빈 행
    ["03/20", "메가커피", "(1,200)"],  # 부분취소 (음수)
]
txs = parse_statement_table(sample_table, 2026, 4)
assert_eq(len(txs), 3, "stmt table row count")
assert_eq(txs[0]["날짜"], "2026-03-12", "stmt table row0 date")
assert_eq(txs[0]["금액"], 5800, "stmt table row0 amount")
assert_eq(txs[0]["내역"], "스타벅스 강남점", "stmt table row0 merchant")
assert_eq(txs[0]["출처"], "BC카드", "stmt table source")
assert_eq(txs[0]["카테고리"], "식비", "stmt table category")
assert_eq(txs[1]["카테고리"], "쇼핑", "stmt table 쇼핑")
assert_eq(txs[2]["유형"], "입금", "stmt table 음수=입금")
assert_eq(txs[2]["금액"], 1200, "stmt table 음수 금액 절대값")

# 텍스트 라인 fallback
sample_text = """
이용일자  가맹점명           이용금액
03/14 메가커피 4,500
03-16 GS25 강남점 12,300
빈줄
04.02 스타벅스코리아 6,500
"""
txs2 = parse_statement_text(sample_text, 2026, 4)
assert_eq(len(txs2), 3, "stmt text row count")
assert_eq(txs2[0]["날짜"], "2026-03-14", "stmt text row0 date")
assert_eq(txs2[1]["금액"], 12300, "stmt text row1 amount")
assert_eq(txs2[2]["내역"], "스타벅스코리아", "stmt text row2 merchant")

# ── 실제 BC카드 명세서 포맷 (다중 행 헤더, 그룹/소계 행, 0원 면제) ──
# 컬럼: 이용일자 | 가맹점명 | 소재지/TOP포인트 | 이용금액 | 할부개월 | 회차 | 원금(KRW) | 수수료 | 특별서비스 | 할인금액 | 결제후잔액
bc_table = [
    # 헤더 1행
    ["이용\n일자", "이용 가맹점(은행)", "", "이용 금액\n(해외현지금액)", "할부\n개월", "회차",
     "이번 달 입금하실 금액", "", "특별\n서비스", "할인 금액", "결제 후 잔액\n(해외청구금액)"],
    # 헤더 2행 (병합 셀의 하위 라벨)
    ["", "가맹점(은행)명", "소재지\nTOP포인트", "", "", "",
     "원금(KRW)\n해외(US$)", "수수료\n(환율)", "", "", ""],
    # 그룹 헤더 행 (skip)
    ["", "(본인) 홍길동 고객님", "(신용)테스트카드_신용 (G430)", "", "", "", "", "", "", "", ""],
    # 일시불: 이용금액 비어있고 원금(KRW)에만 값
    ["04/04", "테스트분식점", "", "", "", "", "47,000", "0", "", "0", "0"],
    # 할부 (이용금액 379,440 / 이번달 청구 75,888)
    ["02/10", "테스트쇼핑몰", "", "379,440", "5", "2", "75,888", "0", "면제", "0", "227,664"],
    # 면제 거래 (모두 0원) → skip
    ["02/05", "휴대폰메시지(승인안내)-02월분 면제", "", "", "", "", "0", "0", "", "0", "0"],
    # 할인 (음수)
    ["04/04", "테스트영화할인", "", "-8,000", "", "", "-8,000", "0", "", "-8,000", "0"],
    # TOP포인트 정보가 소재지 컬럼에 들어 있는 정상 거래
    ["03/21", "테스트옛곳식품", "63", "", "", "", "6,500", "0", "", "0", "0"],
    # 빈 행 (skip)
    ["", "", "", "", "", "", "", "", "", "", ""],
    # 소계/합계 (skip)
    ["", "소계", "", "379,440", "", "", "121,388", "0", "", "-8,000", "227,664"],
    ["", "합계", "", "", "", "", "121,388", "0", "", "-8,000", "227,664"],
]
bc_txs = parse_statement_table(bc_table, 2026, 4)
assert_eq(len(bc_txs), 4, "bc table row count (4 valid)")
assert_eq(bc_txs[0]["날짜"], "2026-04-04", "bc row0 date")
assert_eq(bc_txs[0]["내역"], "테스트분식점", "bc row0 merchant")
assert_eq(bc_txs[0]["금액"], 47000, "bc row0 amount (일시불 원금)")
assert_eq(bc_txs[1]["내역"], "테스트쇼핑몰", "bc row1 merchant")
assert_eq(bc_txs[1]["금액"], 75888, "bc row1 amount (할부 이번달)")
assert_eq(bc_txs[2]["유형"], "입금", "bc row2 (음수=입금)")
assert_eq(bc_txs[2]["금액"], 8000, "bc row2 absolute")
assert_eq(bc_txs[3]["내역"], "테스트옛곳식품", "bc row3 merchant")
assert_eq(bc_txs[3]["금액"], 6500, "bc row3 amount")
# 면제/소계/합계/그룹헤더 모두 제외됐는지 추가 확인
merchants = [t["내역"] for t in bc_txs]
for skipped in ["휴대폰메시지", "(본인)", "소계", "합계"]:
    assert_eq(any(skipped in m for m in merchants), False, f"bc skipped {skipped}")

# ── 안내성 메일 사전 차단 ──
assert_eq(is_non_transaction_subject("위험자산 한도초과 안내"), True, "non-tx 위험자산")
assert_eq(is_non_transaction_subject("[안내] 약관 변경"), True, "non-tx 약관")
assert_eq(is_non_transaction_subject("결제 알림"), False, "non-tx 결제")
assert_eq(is_non_transaction_subject(""), False, "non-tx empty")

# ── _is_amount_token: 할부개월/회차 같은 1~3자리 정수 제외 ──
assert_eq(_is_amount_token("12,345"), True, "amount 콤마")
assert_eq(_is_amount_token("-12,345"), True, "amount 음수 콤마")
assert_eq(_is_amount_token("0"), True, "amount 0")
assert_eq(_is_amount_token("12345"), True, "amount 5자리 무콤마")
assert_eq(_is_amount_token("5"), False, "amount 단자리 (할부개월)")
assert_eq(_is_amount_token("12"), False, "amount 두자리 (회차)")
assert_eq(_is_amount_token("123"), False, "amount 세자리 (애매하지만 제외)")
assert_eq(_is_amount_token("옥션"), False, "amount 한글")
assert_eq(_is_amount_token("(1,500)"), True, "amount 괄호")

# ── parse_statement_text: BC카드 실제 라인 포맷 (한 줄에 여러 숫자) ──
# "옥션 라인": 이용금액 379,440 / 할부5개월 / 2회차 / 원금 75,888 / 수수료 0 / 면제 / 할인 0 / 잔액 227,664
# 첫 비-0 amount = 379,440 (이용금액). 5/2는 할부개월/회차로 무시.
bc_line_text = """이용일자  가맹점명           이용금액
02/10 옥션 379,440 5 2 75,888 0 면제 0 227,664
04/04 또와요짬뽕 47,000 0 0 0
03/22 마트할인 -1,891 -1,891 0 -1,891 0
02/05 휴대폰메시지(승인안내)-02월분 면제 0 0 0
03/26 3대째 소문난 순대국 30,000 0 0 0
04/04 영화할인 -8,000 -8,000 0 -8,000 0
"""
bc_line_txs = parse_statement_text(bc_line_text, 2026, 4)
# 기대: 옥션, 또와요짬뽕, 마트할인, 3대째 소문난 순대국, 영화할인 — 5건 (휴대폰면제 0원 제외)
assert_eq(len(bc_line_txs), 5, "bc text row count (5 valid)")
# 옥션은 첫 비-0 amount = 이용금액 379,440 (잔액 227,664이 아님)
opp = next(t for t in bc_line_txs if t["내역"].startswith("옥션"))
assert_eq(opp["내역"], "옥션", "bc text 옥션 merchant only (5/2 metadata 제외)")
assert_eq(opp["금액"], 379440, "bc text 옥션 이용금액 (잔액 X)")
# 또와요짬뽕 일시불
ttwa = next(t for t in bc_line_txs if "또와요" in t["내역"])
assert_eq(ttwa["금액"], 47000, "bc text 또와요짬뽕 금액")
# 음수 거래
mart = next(t for t in bc_line_txs if t["내역"] == "마트할인")
assert_eq(mart["유형"], "입금", "bc text 마트할인 = 입금")
assert_eq(mart["금액"], 1891, "bc text 마트할인 절대값")
# 다단어 가맹점
sundae = next(t for t in bc_line_txs if "순대국" in t["내역"])
assert_eq(sundae["내역"], "3대째 소문난 순대국", "bc text 다단어 merchant")
assert_eq(sundae["금액"], 30000, "bc text 순대국 금액")

print()
if FAILED:
    print(f"❌ {FAILED}개 테스트 실패")
    sys.exit(1)
print("🎉 모든 테스트 통과")
