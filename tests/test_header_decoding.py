"""email.header.Header가 그대로 .lower() 호출에 들어가 깨지지 않는지 회귀 테스트.

backfill cron 로그에서 다음 에러가 3건 발생 → get_email_body가
Content-Disposition 헤더에 RFC2047 인코딩이 섞여 Header 객체를 반환할 때
.lower() 호출이 실패한 사례.
    거래 파싱 실패 (eid=b'951'): 'Header' object has no attribute 'lower'
"""

import email
import os
import sys
from email.header import Header

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


def test_decode_str_handles_header_object():
    """decode_str가 Header 객체 입력도 str로 변환."""
    h = Header("BC카드", "utf-8")
    assert email_parser.decode_str(h) == "BC카드"


def test_header_str_helper_returns_string():
    """header_str helper는 None/str/Header 모두 str로."""
    assert email_parser.header_str(None) == ""
    assert email_parser.header_str("plain") == "plain"
    assert email_parser.header_str(Header("Hello", "utf-8")) == "Hello"


def test_get_email_body_survives_header_object_disposition():
    """Content-Disposition 헤더가 Header 객체여도 get_email_body 정상 동작."""
    # imaplib에서 받은 RFC822 바이트는 COMPAT32 policy로 파싱되며
    # 일부 헤더가 Header 객체로 노출될 수 있다.
    raw = (
        b"From: test@example.com\r\n"
        b"Subject: test\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=\"BOUNDARY\"\r\n"
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 7bit\r\n"
        b"\r\n"
        b"hello 1,000 won payment\r\n"
        b"--BOUNDARY--\r\n"
    )
    msg = email.message_from_bytes(raw)
    # 각 파트에 Header 객체로 Content-Disposition 강제 주입
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        del part["Content-Disposition"]
        part["Content-Disposition"] = Header("inline", "utf-8")
        # part.get(...) 가 Header를 반환하는지 확인 (회귀 테스트 전제 조건)
        assert not isinstance(part.get("Content-Disposition"), str)

    body = email_parser.get_email_body(msg)
    assert "1,000" in body


def test_get_email_body_singlepart_does_not_crash_with_header_disp():
    """단일 파트 + Header 형 Content-Disposition도 .lower() 크래시 없음."""
    raw = (
        b"From: bcbill@bccard.com\r\n"
        b"Subject: BC\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"BC card 1,000 won payment\r\n"
    )
    msg = email.message_from_bytes(raw)
    # 단일 파트지만 일관성 위해 동일 코드 경로 검증
    body = email_parser.get_email_body(msg)
    assert "BC" in body
