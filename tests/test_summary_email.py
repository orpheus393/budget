"""build_summary_email (SMTP 수집 요약) 회귀 테스트.

'조용한 성공-실패' 방지: 명세서·카뱅 이벤트나 저장이 있을 때만 발신,
매일 빈 결과는 소음이 되지 않게 (None, None).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


def test_silent_when_nothing_happened():
    subject, body = email_parser.build_summary_email(0, {"거래": 0, "쇼핑": 0})
    assert subject is None and body is None


def test_silent_when_only_cleanup_moves():
    """쇼핑/광고 메일 정리만 있었던 평일 — 발신 안 함 (소음 방지)."""
    subject, body = email_parser.build_summary_email(
        0, {"쇼핑": 5, "광고": 3, "SNS": 1})
    assert subject is None


def test_sends_when_saved():
    subject, body = email_parser.build_summary_email(
        43, {"명세서": 1}, parsed_count=43)
    assert "43건 저장" in subject
    assert "BC카드 명세서: 1건 처리" in body
    assert "저장된 거래: 43건" in body


def test_warns_when_all_duplicates():
    """명세서를 처리했는데 전부 중복 → 발신하되 ⚠️ 명시 (침묵 방지 핵심)."""
    subject, body = email_parser.build_summary_email(
        0, {"KB명세서": 1}, parsed_count=12)
    assert subject is not None
    assert "KB카드 명세서: 1건 처리" in body
    assert "전부 중복" in body


def test_kakao_export_event_labeled():
    subject, body = email_parser.build_summary_email(
        30, {"카뱅내보내기": 1}, parsed_count=30)
    assert "카카오뱅크 내보내기: 1건 처리" in body
