"""classify_non_transaction 회귀 테스트 — 쇼핑/광고/SNS/뉴스레터 분류."""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


# ── 쇼핑 발신자 (AliExpress 등 신규 추가) ─────────────
def test_aliexpress_sender_classified_as_shopping():
    """AliExpress 메일은 가계부_쇼핑으로 분류."""
    folder = email_parser.classify_non_transaction(
        "AliExpress <transaction@notice.aliexpress.com>",
        "운송장 번호 520679976456: 배송 완료",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_amazon_sender_classified_as_shopping():
    folder = email_parser.classify_non_transaction(
        "Amazon.com <auto-shipping@amazon.com>",
        "Your order has shipped",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_coupang_existing_sender_still_works():
    """기존 발신자도 회귀 없는지."""
    folder = email_parser.classify_non_transaction(
        "no-reply@coupang.com", "배송완료 안내"
    )
    assert folder == email_parser.SHOPPING_FOLDER


# ── 제목 공백 변형 ─────────────────────────────────────
def test_subject_with_spaces_still_matches():
    """'배송 완료' (공백 포함) 도 '배송완료' 키워드로 매칭."""
    folder = email_parser.classify_non_transaction(
        "AliExpress <x@ali.example>", "운송장 번호: 배송 완료"
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_subject_with_irregular_spaces():
    """전각/연속 공백 변형도 처리."""
    folder = email_parser.classify_non_transaction(
        "unknown@example.com", "주문 확인 안내"
    )
    assert folder == email_parser.SHOPPING_FOLDER  # 주문확인 매칭


def test_운송장_keyword_matches():
    """새로 추가된 '운송장' 키워드 검증."""
    folder = email_parser.classify_non_transaction(
        "noreply@unknown-shop.com", "운송장 번호 12345 안내"
    )
    assert folder == email_parser.SHOPPING_FOLDER


# ── 광고/SNS/뉴스레터 ──────────────────────────────────
def test_promo_subject_classified_as_ad():
    folder = email_parser.classify_non_transaction(
        "noreply@somemall.com", "(광고) 봄맞이 50% 할인 이벤트"
    )
    assert folder == email_parser.AD_FOLDER


def test_sns_sender_classified():
    folder = email_parser.classify_non_transaction(
        "notification@instagram.com", "새 좋아요"
    )
    assert folder == email_parser.SNS_FOLDER


# ── 거래 알림은 None (분류 안 됨) ──────────────────────
def test_transaction_email_returns_none():
    """은행/카드 거래 알림은 None — process_folder가 별도 처리."""
    folder = email_parser.classify_non_transaction(
        "bcbill@bccard.com", "[BC카드] 결제 안내"
    )
    assert folder is None
