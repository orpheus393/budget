"""거래 메일은 '저장 뒤에' 옮긴다 + 실패 알림 원인 추출 회귀 테스트.

2026-10-20·21: 메일을 처리완료로 옮긴 뒤·저장 전에 프로세스가 죽어,
새 거래였다면 메일만 치워지고 데이터는 사라질 뻔했다.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import email_parser
import run_local_fetch


class FakeMail:
    """imaplib 흉내 — 무엇이 언제 불렸는지 기록한다."""

    def __init__(self, uid_by_eid=None):
        self.uid_by_eid = uid_by_eid or {}
        self.calls = []

    def fetch(self, eid, what):
        self.calls.append(("fetch", eid, what))
        uid = self.uid_by_eid.get(eid)
        if uid is None:
            return "OK", [None]
        return "OK", [f"{eid.decode()} (UID {uid})".encode()]

    def store(self, *a):
        self.calls.append(("store",) + a)
        return "OK", []

    def copy(self, *a):
        self.calls.append(("copy",) + a)
        return "OK", []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder))
        return "OK", []

    def uid(self, cmd, *a):
        self.calls.append(("uid", cmd) + a)
        return "OK", []

    def expunge(self):
        self.calls.append(("expunge",))
        return "OK", []

    def logout(self):
        self.calls.append(("logout",))


def setup_function():
    email_parser._DEFERRED_MOVES.clear()


def test_defer_does_not_touch_mailbox():
    """예약 단계에선 UID만 읽고 메일 상태는 바꾸지 않는다."""
    m = FakeMail({b"995": "40001"})
    assert email_parser.defer_move(m, "INBOX", b"995", "DONE") is True
    kinds = {c[0] for c in m.calls}
    assert kinds == {"fetch"}, f"예약 중 메일함 변경 발생: {m.calls}"
    assert email_parser._DEFERRED_MOVES == [("INBOX", "40001", "DONE")]


def test_defer_dedups_same_mail():
    m = FakeMail({b"1": "7"})
    email_parser.defer_move(m, "INBOX", b"1", "DONE")
    email_parser.defer_move(m, "INBOX", b"1", "DONE")
    assert len(email_parser._DEFERRED_MOVES) == 1


def test_defer_without_uid_fails_safely():
    """UID를 못 읽으면 예약하지 않는다 → 메일은 남고 다음 실행에서 재처리."""
    m = FakeMail({})
    assert email_parser.defer_move(m, "INBOX", b"9", "DONE") is False
    assert email_parser._DEFERRED_MOVES == []


def test_flush_moves_by_uid(monkeypatch):
    """옮길 때는 순번이 아니라 UID로 — 정리 패스의 EXPUNGE로 순번이 바뀌기 때문."""
    m = FakeMail({b"5": "111", b"6": "222"})
    email_parser.defer_move(m, "INBOX", b"5", "DONE")
    email_parser.defer_move(m, "INBOX", b"6", "DONE")
    m2 = FakeMail()
    monkeypatch.setattr(email_parser, "connect_imap", lambda: m2)
    assert email_parser.flush_deferred_moves() == 2
    uid_calls = [c for c in m2.calls if c[0] == "uid"]
    assert ("uid", "COPY", "111", '"DONE"') in uid_calls
    assert ("uid", "STORE", "222", "+FLAGS", "\\Deleted") in uid_calls
    assert not any(c[0] in ("copy", "store") for c in m2.calls), "순번 기반 이동이 섞였다"
    assert email_parser._DEFERRED_MOVES == []


def test_flush_noop_without_reservations(monkeypatch):
    called = []
    monkeypatch.setattr(email_parser, "connect_imap", lambda: called.append(1))
    assert email_parser.flush_deferred_moves() == 0
    assert called == [], "예약이 없으면 IMAP에 붙지도 않는다"


def test_kakao_export_is_skipped_by_general_pass():
    """한 메일은 한 패스만 — 카뱅 내보내기는 전용 패스 몫이다."""
    assert email_parser.is_kakao_export_email("[카카오뱅크] 요청하신 거래내역 엑셀 파일입니다")


# ── 실패 알림 원인 추출 ────────────────────────────────
def test_reason_auth_failure():
    out = "imaplib.IMAP4.error: b'[AUTH] \"x@naver.com\": Authentication failed.'"
    assert "앱 비밀번호" in run_local_fetch._failure_reason(out)


def test_reason_picks_last_error_line():
    out = ("[2026-10-21] 이메일 파싱 시작\n파싱된 거래: 0개\nTraceback (most recent call last):\n"
           "  File \"x.py\", line 1\nUnicodeEncodeError: 'cp949' codec can't encode")
    assert run_local_fetch._failure_reason(out).startswith("UnicodeEncodeError")


def test_reason_empty_output():
    assert run_local_fetch._failure_reason("") == "출력 없음"
