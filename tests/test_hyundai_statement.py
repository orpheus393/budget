"""현대카드 이메일 명세서 파서 회귀 테스트.

업로드 파서(app.py)와 같은 컬럼 어휘·금액 규칙을 쓰는지가 핵심 —
같은 거래가 메일과 수동 업로드 양쪽으로 들어와도 중복 키
(날짜_출처_금액_내역)가 일치해야 한 번만 저장된다.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser  # noqa: E402

parse = email_parser.parse_hyundai_statement_html


def _table(header, rows, extra_html=""):
    th = "".join(f"<th>{c}</th>" for c in header)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                  for r in rows)
    return f"<html><body>{extra_html}<table><tr>{th}</tr>{trs}</table></body></html>"


def test_statement_uses_payment_principal_not_total():
    """할부: '결제원금'(이번달 분담액)을 쓰고 전체 이용금액은 무시."""
    html = _table(["이용일", "이용가맹점", "이용금액", "결제원금", "이용구분"],
                  [["2026.07.03", "테스트가구", "600,000", "100,000", "6개월"]])
    txs = parse(html, 2026)
    assert len(txs) == 1
    assert txs[0]["금액"] == 100000
    assert txs[0]["유형"] == "출금"


def test_falls_back_to_usage_amount_when_no_principal():
    html = _table(["이용일자", "가맹점명", "이용금액"],
                  [["2026.07.05", "테스트분식", "12,500"]])
    txs = parse(html, 2026)
    assert txs[0]["금액"] == 12500
    assert txs[0]["날짜"] == "2026-07-05"


def test_negative_principal_becomes_deposit():
    """매출할인·취소는 음수로 와서 입금으로 뒤집히고 절대값 저장."""
    html = _table(["이용일", "가맹점", "결제원금"],
                  [["2026.07.08", "테스트마트", "-8,000"]])
    txs = parse(html, 2026)
    assert txs[0]["유형"] == "입금"
    assert txs[0]["금액"] == 8000


def test_zero_principal_skipped_as_carried_over():
    html = _table(["이용일", "가맹점", "이용금액", "결제원금"],
                  [["2026.07.09", "이월거래", "300,000", "0"]])
    assert parse(html, 2026) == []


def test_subtotal_rows_dropped():
    html = _table(["이용일", "가맹점", "이용금액"],
                  [["2026.07.05", "테스트분식", "12,500"],
                   ["2026.07.31", "국내 일시불 소계", "912,500"],
                   ["", "합계", "912,500"]])
    txs = parse(html, 2026)
    assert [t["내역"] for t in txs] == ["테스트분식"]


def test_column_order_change_is_tolerated():
    """열 위치가 아니라 열 이름으로 찾으므로 순서가 바뀌어도 동작."""
    html = _table(["가맹점명", "이용금액", "이용일"],
                  [["테스트카페", "4,800", "2026.07.11"]])
    txs = parse(html, 2026)
    assert txs[0]["내역"] == "테스트카페" and txs[0]["금액"] == 4800


def test_korean_date_format():
    html = _table(["이용일자", "가맹점", "이용금액"],
                  [["2026년 07월 15일", "테스트주유소", "50,000"]])
    assert parse(html, 2026)[0]["날짜"] == "2026-07-15"


def test_month_day_only_uses_statement_year():
    html = _table(["이용일", "가맹점", "이용금액"],
                  [["07.15", "테스트서점", "20,000"]])
    assert parse(html, 2026)[0]["날짜"] == "2026-07-15"


def test_card_name_kept_in_origin():
    html = _table(["이용일", "가맹점", "이용금액", "이용카드"],
                  [["2026.07.15", "테스트상점", "10,000", "본인 M3"]])
    assert parse(html, 2026)[0]["원문"] == "현대카드 | 본인 M3"


def test_merchant_whitespace_collapsed_for_dedup():
    """메일 HTML 셀의 줄바꿈이 업로드본과 다른 문자열이 되면 중복 제거가 깨진다."""
    html = _table(["이용일", "가맹점", "이용금액"],
                  [["2026.07.15", "테스트  상점\n 2호점", "10,000"]])
    assert parse(html, 2026)[0]["내역"] == "테스트 상점 2호점"


def test_layout_table_without_headers_ignored():
    """명세서 메일 상단의 레이아웃용 표는 건너뛴다."""
    html = _table(["이용일", "가맹점", "이용금액"],
                  [["2026.07.15", "테스트상점", "10,000"]],
                  extra_html="<table><tr><td>로고</td><td>안내문</td></tr></table>")
    assert len(parse(html, 2026)) == 1


def test_source_and_category_filled():
    html = _table(["이용일", "가맹점", "이용금액"],
                  [["2026.07.15", "스타벅스", "5,000"]])
    tx = parse(html, 2026)[0]
    assert tx["출처"] == "현대카드"
    assert tx["카테고리"]


def test_no_table_returns_empty():
    assert parse("<html><body>명세서는 첨부를 확인하세요</body></html>", 2026) == []


def test_diagnostic_reports_structure_without_leaking_content():
    html = _table(["이용일", "가맹점", "이용금액"],
                  [["2026.07.15", "비밀가맹점", "123,456"]])
    desc = email_parser.describe_hyundai_html(html)
    assert "표 1개" in desc and "헤더후보" in desc
    assert "비밀가맹점" not in desc and "123,456" not in desc


def test_diagnostic_when_no_table():
    assert "표 없음" in email_parser.describe_hyundai_html("<html></html>")
