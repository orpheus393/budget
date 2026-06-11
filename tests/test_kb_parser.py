"""KB국민카드 이메일 명세서 HTML 파서 회귀 테스트."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


SAMPLE_KB_HTML = """\
<html><body>
<script>
var list_pe00Json = [{"청구일련번호" : 1, "카드고객명" : "홍*동",
"결제년월일" : "2026년 06월 15일",
"PRD1" : "2026.05.02 ~ 2026.06.01",
"결제금액" : "         123,456",
"일시불이용금액" : "         100,000"},];

var list_pe01Json = [
{"청구일련번호" : 1, "data" : '<tr><td class="first">26.05.03</td><td>국내099</td><td>일시불</td><td><a href="x"><u>스타벅스코엑스</u></a></td><td>&nbsp;</td><td><span class="sum">5,500</span></td><td>&nbsp;</td><td>&nbsp;</td><td><span class="sum">5,500</span></td></tr>'},
{"청구일련번호" : 1, "data" : '<tr><td class="first">26.05.10</td><td>국내099</td><td>일시불</td><td><a href="y"><u>쿠팡&#40;쿠페이&#41;-쿠팡&#40;쿠페이&#41;</u></a></td><td>&nbsp;</td><td><span class="sum">17,800</span></td><td>&nbsp;</td><td>&nbsp;</td><td><span class="sum">17,800</span></td></tr>'},
{"청구일련번호" : 1, "data" : '<tr><td class="first">26.05.15</td><td>국내099</td><td>할부 3개월</td><td><a href="z"><u>병원검진</u></a></td><td>&nbsp;</td><td><span class="sum">90,000</span></td><td>&nbsp;</td><td>&nbsp;</td><td><span class="sum">30,000</span></td></tr>'},
];
</script>
</body></html>
"""


def test_parse_kb_email_html_basic():
    txs = email_parser.parse_kb_email_html(SAMPLE_KB_HTML)
    assert len(txs) == 3
    # 첫 거래: 스타벅스 (일시불 → td5 == td8)
    assert txs[0]["날짜"] == "2026-05-03"
    assert txs[0]["출처"] == "KB카드"
    assert txs[0]["유형"] == "출금"
    assert txs[0]["금액"] == 5500
    assert "스타벅스" in txs[0]["내역"]
    # 두 번째: 쿠팡 (HTML entity 디코딩 확인)
    assert txs[1]["내역"] == "쿠팡(쿠페이)-쿠팡(쿠페이)"
    assert txs[1]["금액"] == 17800
    # 세 번째: 할부 — 전체 90,000원이지만 이번달 분담분 30,000원만 가져와야 함
    # (회계적으로 옳은 것 = IBK 자동이체로 빠지는 금액과 정합)
    assert txs[2]["금액"] == 30000
    assert "할부" in txs[2]["원문"]


def test_parse_kb_email_html_includes_billing_month():
    """결제년월일이 원문에 청구월로 포함되는지."""
    txs = email_parser.parse_kb_email_html(SAMPLE_KB_HTML)
    assert all("2026년 06월" in t["원문"] for t in txs)


def test_parse_kb_email_html_assigns_category():
    """guess_category가 적용되는지."""
    txs = email_parser.parse_kb_email_html(SAMPLE_KB_HTML)
    # 스타벅스 → 식비, 쿠팡 → 쇼핑, 병원 → 의료
    assert txs[0]["카테고리"] == "식비"
    assert txs[1]["카테고리"] == "쇼핑"
    assert txs[2]["카테고리"] == "의료"


def test_parse_kb_email_html_empty_when_no_data():
    """pe01 변수가 없으면 빈 리스트."""
    assert email_parser.parse_kb_email_html("<html></html>") == []
    assert email_parser.parse_kb_email_html("") == []


def test_parse_kb_email_html_skips_invalid_rows():
    """잘못된 날짜·금액 행은 건너뜀."""
    bad_html = """
    var list_pe01Json = [
    {"청구일련번호" : 1, "data" : '<tr><td class="first">badDate</td><td>x</td><td>x</td><td>x</td><td></td><td>1000</td><td></td><td></td><td>1000</td></tr>'},
    {"청구일련번호" : 1, "data" : '<tr><td class="first">26.01.01</td><td>x</td><td>x</td><td>식당</td><td></td><td>notnum</td><td></td><td></td><td>notnum</td></tr>'},
    {"청구일련번호" : 1, "data" : '<tr><td class="first">26.01.02</td><td>x</td><td>x</td><td>식당</td><td></td><td>5,000</td><td></td><td></td><td>5,000</td></tr>'},
    ];
    """
    txs = email_parser.parse_kb_email_html(bad_html)
    assert len(txs) == 1
    assert txs[0]["금액"] == 5000


def test_parse_kb_email_html_skips_short_rows():
    """td 9개 미만 행(합계·안내 행)은 건너뜀."""
    # KB는 합계/안내 행이 td 8개로 들어옴 — 정상 거래 13개와 구분
    short_html = """
    var list_pe01Json = [
    {"청구일련번호" : 1, "data" : '<tr><td>합계</td><td></td><td></td><td>622,859</td><td></td><td></td><td></td><td></td></tr>'},
    {"청구일련번호" : 1, "data" : '<tr><td class="first">26.01.05</td><td>국내099</td><td>일시불</td><td>식당</td><td></td><td>10,000</td><td></td><td></td><td>10,000</td></tr>'},
    ];
    """
    txs = email_parser.parse_kb_email_html(short_html)
    assert len(txs) == 1
    assert txs[0]["금액"] == 10000


def test_parse_kb_email_html_installment_uses_share_not_total():
    """할부 거래는 분담분(td8)을 가져와야지, 전체(td5)가 아님 — 결제금액과 정합."""
    # 100,000원 / 5개월 / 2회차 = 분담 20,000원 + 수수료 1,000원
    halbu_html = """
    var list_pe01Json = [
    {"청구일련번호" : 1, "data" : '<tr><td class="first">26.01.10</td><td>국내099</td><td>할부 5개월</td><td>병원</td><td></td><td>100,000</td><td>5</td><td>2</td><td>20,000</td><td>1,000</td></tr>'},
    ];
    """
    txs = email_parser.parse_kb_email_html(halbu_html)
    assert len(txs) == 1
    assert txs[0]["금액"] == 20000  # 분담분
    assert txs[0]["금액"] != 100000  # 전체 아님


def test_is_statement_email_handles_kb_subject():
    """KB의 '이메일명세서' / 'e-메일명세서' 제목도 명세서로 인식."""
    assert email_parser.is_statement_email("KB국민카드 이메일명세서")
    assert email_parser.is_statement_email("KB국민카드 e-메일명세서")
    assert email_parser.is_statement_email("BC카드 이용대금명세서")
    # 일반 결제 알림은 False
    assert not email_parser.is_statement_email("[KB국민카드] 결제 안내")


def test_is_statement_email_kb_resend_and_excludes_setup():
    """KB '명세서 재발송'(실제 KB 형식)은 명세서로, '수령방법' 안내는 제외."""
    assert email_parser.is_statement_email(
        "(KB국민카드) 임*재님 2026년 06월 명세서 재발송"
    )
    assert email_parser.is_statement_email(
        "(KB국민카드) 임*재님 2026년 05월 명세서 재발송"
    )
    assert not email_parser.is_statement_email(
        "(KB국민카드) 임*재님! KB국민카드 명세서 수령방법이 이메일로 신청완료!"
    )
