"""BC카드 명세서 이중계상 회귀 테스트.

명세서가 온누리상품권·정부지원금 결제를 '라벨+가맹점' 줄과 '가맹점만' 줄로
두 번 적어 같은 거래가 2건이 되던 버그 (2026-05~08 실제 17쌍 289,610원 과다).
샘플은 전부 실제 budget.db에서 나온 문자열.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


def tx(date, merchant, amount):
    return {"날짜": date, "내역": merchant, "금액": amount, "출처": "BC카드(신용)"}


def test_strip_payment_label():
    f = email_parser.strip_payment_label
    assert f("온누리전자상품권 ._ 빵가득한집") == "빵가득한집"
    assert f("온누리전자상품권빵가득한집") == "빵가득한집"
    assert f("고유가피해지원금물총칼국수군포점") == "물총칼국수군포점"
    assert f("온누전자상품권부산어묵") == "부산어묵"          # OCR 오독 (리 누락)
    assert f("부산어묵") == "부산어묵"                        # 라벨 없으면 그대로


def test_strip_keeps_label_only_names():
    """라벨만 있는 이름을 빈 문자열로 만들지 않는다."""
    assert email_parser.strip_payment_label("온누리전자상품권") == "온누리전자상품권"
    assert email_parser.strip_payment_label("") == ""


def test_dedup_collapses_label_pair():
    out = email_parser._dedup_pdf_transactions([
        tx("2026-05-05", "온누리전자상품권 ._ 빵가득한집", 9500),
        tx("2026-05-05", "빵가득한집", 9500),
    ])
    assert len(out) == 1
    assert out[0]["내역"] == "빵가득한집"


def test_dedup_survives_ocr_noise_on_both_sides():
    """맛니/맛나처럼 가맹점명 자체가 오독돼도 접힌다."""
    out = email_parser._dedup_pdf_transactions([
        tx("2026-08-01", "온누리전자상품권맛니제과", 6200),
        tx("2026-08-01", "맛나제과", 6200),
    ])
    assert len(out) == 1
    assert out[0]["내역"] == "맛나제과"


def test_dedup_prefers_cleaner_name():
    """라벨 없는 쪽이 OCR 잡음투성이면 깨끗한 쪽을 남긴다."""
    out = email_parser._dedup_pdf_transactions([
        tx("2026-05-09", "_7@0 알찬농산", 7000),
        tx("2026-05-09", "온누리전자상품권 _ 알찬농산", 7000),
    ])
    assert len(out) == 1
    assert out[0]["내역"] == "알찬농산"


def test_dedup_keeps_genuinely_separate_transactions():
    """같은 날·같은 금액이어도 가맹점이 다르면 별개 거래 — 지우면 안 된다."""
    out = email_parser._dedup_pdf_transactions([
        tx("2025-09-12", "ATM출금", 100000),
        tx("2025-09-12", "한명구(부의)", 100000),
        tx("2025-09-12", "쿠팡", 100000),
    ])
    assert len(out) == 3


def test_dedup_keeps_same_merchant_different_amount():
    out = email_parser._dedup_pdf_transactions([
        tx("2026-05-09", "교동닭강정 # 새우강정", 14000),
        tx("2026-05-10", "교동닭강정 # 새우강정", 30000),
    ])
    assert len(out) == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("전부 통과")
