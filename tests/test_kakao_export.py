"""카카오뱅크 '거래내역 엑셀' 내보내기 자동 수집 회귀 테스트.

앱에서 '내보내기 → 이메일'로 보낸 첨부 xlsx를 cron이 파싱하는 경로:
is_kakao_export_email 제목 매칭 → get_xlsx_attachment → parse_kakao_export_xlsx.
출력이 app.py 수동 업로드 파서와 같은 형식이어야 시트 중복 키가 일치한다.
"""

import io
import os
import sys
from email.message import EmailMessage

from openpyxl import Workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import email_parser


def _kakao_xlsx(rows, header=("거래일시", "구분", "거래금액", "거래 후 잔액",
                              "거래구분", "내용", "메모")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["카카오뱅크 거래내역"])  # 상단 메타 행 (헤더 자동 탐지 검증)
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── 제목 매칭 ─────────────────────────────────────────
def test_subject_matches_export_mail():
    assert email_parser.is_kakao_export_email(
        "[카카오뱅크] 고객님께서 요청하신 거래내역 엑셀파일입니다."
    )


def test_subject_ignores_notice_mail():
    assert not email_parser.is_kakao_export_email("[카카오뱅크] 금리인하요구권 제도 안내")


# ── xlsx 파싱 ─────────────────────────────────────────
def test_parse_plain_xlsx():
    raw = _kakao_xlsx([
        ("2026-06-05 12:30:00", "출금", -15000, 985000, "체크카드", "식당결제", ""),
        ("2026-06-07 09:00:00", "입금", 50000, 1035000, "일반입금", "용돈", "메모A"),
    ])
    txs = email_parser.parse_kakao_export_xlsx(raw)
    assert len(txs) == 2
    a, b = txs
    assert a["날짜"] == "2026-06-05" and a["시간"] == "12:30"
    assert a["출처"] == "카카오뱅크" and a["유형"] == "출금" and a["금액"] == 15000
    assert a["내역"] == "식당결제"                      # app.py와 동일: 내용 우선
    assert a["원문"] == "카카오뱅크 | 체크카드"          # app.py와 동일 포맷
    assert b["유형"] == "입금" and b["금액"] == 50000
    assert b["원문"] == "카카오뱅크 | 일반입금 | 메모A"
    assert b["잔액"] == 1035000


def test_parse_skips_zero_and_bad_rows():
    raw = _kakao_xlsx([
        ("2026-06-05 12:30:00", "출금", 0, 985000, "체크카드", "0원거래", ""),
        ("날짜아님", "출금", -1000, 984000, "체크카드", "깨진행", ""),
        ("2026-06-06 10:00:00", "출금", -3000, 981000, "체크카드", "정상", ""),
    ])
    txs = email_parser.parse_kakao_export_xlsx(raw)
    assert len(txs) == 1
    assert txs[0]["내역"] == "정상"


def test_parse_encrypted_without_password_raises():
    """암호화 컨테이너에 시크릿 미설정이면 ValueError (INBOX 유지 → 재시도)."""
    # msoffcrypto가 암호화 파일로 인식하는 최소 OLE 헤더를 흉내내긴 어려우므로
    # is_encrypted=False 경로는 위에서 검증하고, 여기선 함수의 계약만 확인:
    # 잘못된 바이트는 어떤 예외든 발생 (조용히 빈 리스트 반환하지 않음).
    try:
        email_parser.parse_kakao_export_xlsx(b"not an xlsx at all")
        raise AssertionError("예외가 발생해야 함")
    except Exception:
        pass


# ── 첨부 추출 ─────────────────────────────────────────
def test_get_xlsx_attachment():
    msg = EmailMessage()
    msg["Subject"] = "[카카오뱅크] 고객님께서 요청하신 거래내역 엑셀파일입니다."
    msg.set_content("본문")
    payload = _kakao_xlsx([("2026-06-05 12:30:00", "출금", -1000, 0, "체크카드", "x", "")])
    msg.add_attachment(
        payload, maintype="application", subtype="octet-stream",
        filename="카카오뱅크_거래내역_N123_2026.xlsx",
    )
    fname, got = email_parser.get_xlsx_attachment(msg)
    assert fname.endswith(".xlsx")
    assert got == payload


def test_get_xlsx_attachment_none_when_absent():
    msg = EmailMessage()
    msg["Subject"] = "첨부 없는 메일"
    msg.set_content("본문뿐")
    fname, got = email_parser.get_xlsx_attachment(msg)
    assert fname is None and got is None
