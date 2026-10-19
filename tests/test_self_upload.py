"""셀프 업로드 회귀 테스트 — 내가 나에게 보낸 메일 첨부의 자동 적재.

현대카드·IBK는 건별 알림 메일이 없어 파일을 받아야만 한다. 대시보드 업로드
대신 메일 첨부로 같은 파서를 태우는 경로(process_self_uploads)를 검증한다.
"""

import email
import os
import sys
from email.message import EmailMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import email_parser
import upload_parsers


IBK_HTML = """
<html><body>
<table><tr><td>거래내역조회_입출식</td></tr></table>
<table>
<tr>
  <th>No</th><th>거래일시</th><th>출금</th><th>입금</th>
  <th>거래후잔액</th><th>거래내용</th><th>송금메시지</th>
  <th>상대계좌번호</th><th>상대은행</th><th>거래구분</th>
  <th>수표어음금액</th><th>CMS코드</th><th>상대계좌예금주명</th>
</tr>
<tr>
  <td>1</td><td>2026-08-11 09:56:00</td><td>0</td><td>38,000</td>
  <td>1,000,000</td><td>테스트입금</td><td></td>
  <td></td><td>농협</td><td>일반입금</td><td></td><td></td><td>홍길동</td>
</tr>
<tr>
  <td>2</td><td>2026-08-12 13:20:00</td><td>12,500</td><td>0</td>
  <td>987,500</td><td>편의점결제</td><td></td>
  <td></td><td></td><td>체크카드</td><td></td><td></td><td></td>
</tr>
</table>
</body></html>
"""


def _mail_with(attachments, sender="orpheus20@naver.com"):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = sender
    m["Subject"] = "가계부 자료"
    m.set_content("첨부 확인")
    for name, data in attachments:
        m.add_attachment(data, maintype="application", subtype="octet-stream",
                         filename=name)
    return email.message_from_bytes(m.as_bytes())


# ── 첨부 선별 ──────────────────────────────────────────
def test_iter_picks_only_data_files():
    msg = _mail_with([
        ("거래내역.xls", IBK_HTML.encode("utf-8")),
        ("고양이.jpg", b"\xff\xd8\xff\xe0binary"),
        ("메모.txt", b"hello"),
    ])
    names = [n for n, _ in email_parser.iter_upload_attachments(msg)]
    assert names == ["거래내역.xls"]


def test_iter_handles_mail_without_attachment():
    assert list(email_parser.iter_upload_attachments(_mail_with([]))) == []


# ── DataFrame → 거래 dict ──────────────────────────────
def test_dataframe_to_transactions_marks_input_path():
    _, df = upload_parsers.parse_any_file("거래내역.xls", IBK_HTML.encode("utf-8"))
    txs = email_parser.dataframe_to_transactions(df, "IBK기업은행", "거래내역.xls")
    assert txs, "IBK 샘플에서 거래가 나와야 한다"
    for t in txs:
        # 대시보드 업로드('수동:')와 반드시 구분돼야 한다
        assert t["입력경로"] == "자동:메일첨부:IBK기업은행"
        assert t["금액"] > 0
        assert set(t) >= {"날짜", "시간", "출처", "유형", "금액", "내역",
                          "카테고리", "원문", "잔액", "입력경로"}
        assert "거래내역.xls" in t["원문"]


def test_dataframe_to_transactions_drops_zero_and_bad_amounts():
    import pandas as pd
    df = pd.DataFrame([
        {"날짜": "2026-08-01", "금액": 0, "내역": "0원행"},
        {"날짜": "2026-08-02", "금액": "숫자아님", "내역": "깨진행"},
        {"날짜": "2026-08-03", "금액": 5000, "내역": "정상"},
    ])
    txs = email_parser.dataframe_to_transactions(df, "현대카드", "x.csv")
    assert [t["내역"] for t in txs] == ["정상"]


def test_dataframe_to_transactions_defaults_missing_columns():
    import pandas as pd
    df = pd.DataFrame([{"날짜": "2026-08-03", "금액": 5000, "내역": "가맹점"}])
    t = email_parser.dataframe_to_transactions(df, "현대카드", "x.csv")[0]
    assert t["출처"] == "현대카드"
    assert t["유형"] == "출금"
    assert t["카테고리"] == "기타"


# ── 감지 실패는 조용히 건너뛴다 ─────────────────────────
def test_unknown_attachment_raises_so_caller_can_skip():
    import pytest
    with pytest.raises(ValueError):
        upload_parsers.parse_any_file("아무거나.csv", b"this,is,not,a,bank,export\n1,2,3,4,5")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("전부 통과")
