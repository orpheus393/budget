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


# ── PG사/구독/영수증/고지서 (카드 알림 외 결제·주문 확인 메일) ──
def test_easypay_receipt_classified_as_shopping():
    """이지페이 결제 확인 메일도 쇼핑 폴더로."""
    folder = email_parser.classify_non_transaction(
        "easypay_noreturn@easypay.co.kr",
        "임*재님, 쿠팡(주)에서 [신용카드]결제하신 내역입니다.",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_kcp_payment_classified_as_shopping():
    """NHN KCP 결제 내역 메일도 정리."""
    folder = email_parser.classify_non_transaction(
        "pgadmcust@kcp.co.kr",
        "NHN KCP - 쿠팡(쿠페이)의 결제 내역입니다.",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_apartment_bill_classified_as_shopping():
    """아파트아이 관리비 고지서."""
    folder = email_parser.classify_non_transaction(
        "aptibill@apti.co.kr",
        "[아파트아이]아파트관리비 05월 고지서입니다.",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_apple_receipt_classified_as_shopping():
    """Apple 영수증."""
    folder = email_parser.classify_non_transaction(
        "no_reply@email.apple.com",
        "Apple에서 발행한 영수증입니다.",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_tving_subscription_classified_as_shopping():
    """TVING 정기결제 예정 안내."""
    folder = email_parser.classify_non_transaction(
        "no-reply@tving.com",
        "[TVING] 정기결제 예정 내역 안내",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_merchant_order_summary_classified_as_shopping():
    """가야미 주문내역서 — '주문내역' 신규 키워드."""
    folder = email_parser.classify_non_transaction(
        "gayamy@gayamy.co.kr",
        "가야미 주문내역서 확인 메일입니다.",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_payment_subject_alone_catches_unknown_sender():
    """미등록 발신자라도 '결제하신' 제목 키워드로 매칭."""
    folder = email_parser.classify_non_transaction(
        "unknown@random-pg.example.com",
        "임*재님, 어떤가맹점에서 결제하신 내역입니다.",
    )
    assert folder == email_parser.SHOPPING_FOLDER


def test_card_alert_not_misclassified_by_new_keywords():
    """카드사 알림 제목('결제 안내')은 새 키워드와 충돌 없이 None 유지.

    카드 알림은 SENDER_PATTERNS가 먼저 잡으므로 classify_non_transaction
    까지 도달하지 않지만, 만에 하나 미등록 카드 발신자라도 잘못 쇼핑
    분류되면 안 되므로 가드.
    """
    folder = email_parser.classify_non_transaction(
        "noreply@some-card-issuer.example",
        "[카드] 결제 안내 - 1,000원 승인",
    )
    assert folder is None
