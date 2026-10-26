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


# ── 네이버 '최근 1,000통' 창 축소로 최신 메일이 빠지던 문제 ─────────────
class ShrinkingWindowMail:
    """네이버 IMAP 흉내: 삭제 표시된 메일은 즉시 창에서 빠지고, 순번은 남은 메일
    기준으로 다시 매겨진다. 범위를 벗어난 순번을 FETCH하면 [None]을 돌려준다
    (실측: 'NoneType' object is not subscriptable)."""

    def __init__(self, mails):
        # mails: [(uid, from, subject)]
        self.msgs = [{"uid": str(u), "raw": self._raw(f, s), "deleted": False}
                     for u, f, s in mails]
        self.none_fetches = 0
        self.copied = []  # (uid, dest)

    @staticmethod
    def _raw(frm, subject):
        from email.header import Header
        return (f"From: {Header(frm, 'utf-8').encode()}\r\n"
                f"Subject: {Header(subject, 'utf-8').encode()}\r\n"
                "Date: Mon, 26 Oct 2026 08:00:00 +0900\r\n\r\nbody").encode()

    def _visible(self):
        return [m for m in self.msgs if not m["deleted"]]

    def select(self, folder, readonly=False):
        return "OK", [str(len(self._visible())).encode()]

    def search(self, charset, criteria):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self._visible())))]

    def fetch(self, eid, what):
        vis = self._visible()
        i = int(eid) - 1
        if i >= len(vis):
            if "RFC822" in what:
                self.none_fetches += 1
            return "OK", [None]
        m = vis[i]
        if "UID" in what and "RFC822" not in what:
            return "OK", [f"{eid.decode()} (UID {m['uid']})".encode()]
        return "OK", [(f"{eid.decode()} (RFC822".encode(), m["raw"]), b")"]

    def _by_seq(self, eid):
        vis = self._visible()
        i = int(eid) - 1
        return vis[i] if i < len(vis) else None

    def store(self, eid, op, flag):          # 순번 기반(옛 경로)
        m = self._by_seq(eid)
        if m and "Deleted" in flag:
            m["deleted"] = True
        return "OK", []

    def copy(self, eid, dest):               # 순번 기반(옛 경로)
        m = self._by_seq(eid)
        if m:
            self.copied.append((m["uid"], dest))
        return "OK", []

    def uid(self, cmd, uid, *a):
        m = next((x for x in self.msgs if x["uid"] == uid and not x["deleted"]), None)
        if m is None:
            return "NO", []
        if cmd == "COPY":
            self.copied.append((uid, a[0]))
        elif cmd == "STORE" and "Deleted" in a[-1]:
            m["deleted"] = True
        return "OK", []

    def expunge(self):
        return "OK", []


def test_cleanup_moves_do_not_hide_newest_mails(monkeypatch):
    """쇼핑 메일을 루프 도중 옮기면 창이 줄어 끝 순번(최신 메일)을 못 읽었다.
    모든 메일을 다 읽은 뒤에 UID로 옮겨야 한다."""
    monkeypatch.setattr(email_parser, "ENABLE_EMAIL_CLEANUP", True)
    shop = ("쿠팡 <noreply@e.coupang.com>", "[쿠팡] 주문하신 내역을 확인해주세요")
    other = ("친구 <friend@example.com>", "안녕")
    m = ShrinkingWindowMail([
        (101, *shop), (102, *shop), (103, *shop),     # 앞쪽 = 오래된 쇼핑 메일
        (104, *other), (105, *other), (106, *other),  # 뒤쪽 = 가장 최근 메일
    ])
    dest = {"가계부_쇼핑": "SHOP"}
    _, moved = email_parser.process_folder(m, "INBOX", 26, dest, {})

    assert m.none_fetches == 0, f"최신 메일 {m.none_fetches}통을 못 읽었다"
    assert moved["쇼핑"] == 3
    assert sorted(m.copied) == [("101", '"SHOP"'), ("102", '"SHOP"'), ("103", '"SHOP"')], \
        "쇼핑 메일만, 정확히 그 메일만 옮겨야 한다"
    assert [x["uid"] for x in m._visible()] == ["104", "105", "106"]
