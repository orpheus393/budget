"""이메일 정리 요약(build_cleanup_summary) 회귀 테스트."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


def test_summary_lists_categories_and_subjects():
    log = {
        "광고": ["(광고) 세일", "[이벤트] 쿠폰", "특가"],
        "쇼핑": ["[쿠팡] 배송완료"],
    }
    moved = {"광고": 3, "쇼핑": 1, "거래": 0, "명세서": 0}
    out = email_parser.build_cleanup_summary(log, moved)
    assert "이메일 정리 요약" in out
    assert "광고: 3건" in out
    assert "(광고) 세일" in out
    assert "쇼핑: 1건" in out
    assert "[쿠팡] 배송완료" in out


def test_summary_marks_promo_as_read():
    """광고 카테고리에 '읽음 처리' 표기."""
    log = {"광고": ["(광고) 알림"]}
    moved = {"광고": 1}
    out = email_parser.build_cleanup_summary(log, moved)
    assert "읽음 처리" in out
    # 비광고는 읽음 표기 없음
    log2 = {"쇼핑": ["주문확인"]}
    out2 = email_parser.build_cleanup_summary(log2, {"쇼핑": 1})
    assert "읽음 처리" not in out2


def test_summary_truncates_long_lists():
    """3건 초과는 '외 N건'으로 축약."""
    log = {"광고": [f"광고{i}" for i in range(10)]}
    moved = {"광고": 10}
    out = email_parser.build_cleanup_summary(log, moved)
    assert "외 7건" in out  # 10 - 3


def test_summary_empty_when_nothing_cleaned():
    out = email_parser.build_cleanup_summary({}, {"거래": 0})
    assert "정리할 비거래 메일 없음" in out


def test_summary_includes_transaction_count():
    out = email_parser.build_cleanup_summary({}, {"거래": 5, "명세서": 2, "KB명세서": 1})
    assert "거래 알림 5건" in out
    assert "명세서 3건" in out  # 2 + 1
