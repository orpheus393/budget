"""주택금융공사 보금자리론 안내 파싱(parse_loan_notice) 회귀 테스트."""

import app


REAL_NOTICE = """\
[주택금융공사]06월 18일은 고객님의 보금자리론 116회차 원리금납입일입니다.
■06월 11일 08시 기준 대출잔액 114,051,749원
■금번 회차 납입액은 총:729,960원(원금:465,517 이자:264,443)입니다.
"""


def test_parse_loan_notice_real_message():
    """사용자가 받은 실제 안내 메시지."""
    out = app.parse_loan_notice(REAL_NOTICE, base_year=2026)
    assert out is not None
    assert out["회차"] == 116
    assert out["납입액"] == 729960
    assert out["원금"] == 465517
    assert out["이자"] == 264443
    assert out["잔액"] == 114051749
    assert out["납입일"] == "2026-06-18"


def test_parse_loan_notice_returns_none_for_bad_text():
    assert app.parse_loan_notice("") is None
    assert app.parse_loan_notice("관계없는 텍스트") is None
    # 필드 일부만 있을 때도 None
    partial = "06월 18일은 ... 116회차"
    assert app.parse_loan_notice(partial) is None


def test_parse_loan_notice_amount_sanity():
    """납입액 = 원금 + 이자 검증 (실제 안내는 항상 이렇게 옴)."""
    out = app.parse_loan_notice(REAL_NOTICE, base_year=2026)
    assert out["원금"] + out["이자"] == out["납입액"]


def test_parse_loan_notice_uses_explicit_year():
    """base_year를 명시하면 해당 연도로 납입일 구성."""
    out = app.parse_loan_notice(REAL_NOTICE, base_year=2025)
    assert out["납입일"].startswith("2025-")


def test_parse_loan_notice_handles_spaces_and_punct():
    """안내 문자가 공백·콜론 변형돼도 파싱 가능해야."""
    variant = """[주택금융공사] 07월 10일은 117회차 원리금납입일입니다.
■대출잔액 113,500,000원
■총 : 730,000원 (원금 : 470,000 이자 : 260,000)
"""
    out = app.parse_loan_notice(variant, base_year=2026)
    assert out is not None
    assert out["회차"] == 117
    assert out["원금"] == 470000
    assert out["이자"] == 260000
    assert out["잔액"] == 113500000
