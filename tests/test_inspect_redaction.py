"""inspect_email의 형태 출력(shape) 회귀 테스트.

저장소가 public이면 Actions 로그도 공개된다. inspect는 기본적으로 내용 대신
레이아웃 형태만 출력해야 한다 — 가맹점·금액·카드번호가 로그에 남으면 안 된다.
"""

import os
import sys

os.environ.setdefault("NAVER_EMAIL", "t@example.com")
os.environ.setdefault("NAVER_APP_PW", "dummy")
os.environ.setdefault("EMAIL_INSPECT_FROM", "hyundaicard")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import inspect_email  # noqa: E402


def test_merchant_and_amount_are_not_leaked():
    out = inspect_email.shape("2026.07.15  스타벅스강남점  12,500원")
    assert "스타벅스" not in out and "12,500" not in out


def test_date_separators_survive_for_parser_design():
    assert inspect_email.shape("2026.07.15") == "#4.#2.#2"


def test_amount_comma_folds_into_one_token():
    assert inspect_email.shape("12,500") == "#6"


def test_column_layout_preserved():
    """열 구분(공백)과 열 개수가 보존돼야 파서를 설계할 수 있다."""
    out = inspect_email.shape("2026.07.15\t가맹점\t10,000\t일시불")
    assert out.count("\t") == 3


def test_card_number_masked():
    """카드번호는 자리수 표기(#4)만 남고 원래 숫자는 사라진다.

    출력의 '#4'에도 숫자 문자가 들어가므로 '숫자 없음'이 아니라
    '원문 토큰이 남지 않음'을 검증한다.
    """
    out = inspect_email.shape("1234-5678-9012-3456")
    assert out == "#4-#4-#4-#4"
    assert "1234" not in out and "9012" not in out


def test_latin_and_hangul_lengths_reported():
    assert inspect_email.shape("CJ CGV") == "A2 A3"
    assert inspect_email.shape("현대카드") == "가4"


def test_punctuation_kept():
    assert inspect_email.shape("[현대카드] (주)이마트") == "[가4] (가1)가3"


def test_empty_string():
    assert inspect_email.shape("") == ""
