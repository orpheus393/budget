"""parse_any_file / find_bc_echo_rows 회귀 테스트 — 통합 업로드 인박스.

파일 내용으로 기관을 감지해 알맞은 파서로 보내는 로직과,
BC카드(체크) 명세서 ↔ IBK 통장 echo 중복 감지를 검증.
"""

import io

import pandas as pd

import app


# ── 샘플 파일 생성기 ───────────────────────────────────
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
  <td>1</td><td>2026-05-31 09:56:00</td><td>0</td><td>38,000</td>
  <td>1,000,000</td><td>테스트입금</td><td></td>
  <td></td><td>농협</td><td>일반입금</td><td></td><td></td><td>홍길동</td>
</tr>
<tr>
  <td>2</td><td>2026-05-31 12:00:00</td><td>15,000</td><td>0</td>
  <td>985,000</td><td>테스트식당</td><td></td>
  <td></td><td></td><td>체크카드</td><td></td><td></td><td></td>
</tr>
</table>
</body></html>
""".encode("utf-8")

HYUNDAI_CSV = (
    "이용일,이용가맹점,이용금액\n"
    "2026-05-01,스타벅스코엑스,5500\n"
    "2026-05-02,김밥천국,4000\n"
).encode("utf-8")


def _kakao_xlsx_bytes() -> bytes:
    df = pd.DataFrame({
        "거래일시": ["2026-05-01 12:00:00", "2026-05-02 18:30:00"],
        "구분": ["출금", "입금"],
        "거래금액": [-15000, 50000],
        "거래 후 잔액": [985000, 1035000],
        "거래구분": ["체크카드", "일반입금"],
        "내용": ["식당결제", "용돈"],
        "메모": ["", ""],
    })
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


# ── parse_any_file: 내용 기반 감지 ────────────────────
def test_detect_ibk_html():
    kind, df = app.parse_any_file("거래내역.xls", IBK_HTML)
    assert kind == "IBK기업은행"
    assert len(df) == 2
    assert set(df["출처"]) == {"IBK기업은행"}


def test_detect_hyundai_csv():
    kind, df = app.parse_any_file("hyundai.csv", HYUNDAI_CSV)
    assert kind == "현대카드"
    assert len(df) == 2
    assert set(df["출처"]) == {"현대카드"}


def test_detect_kakao_xlsx():
    kind, df = app.parse_any_file("카카오뱅크_거래내역.xlsx", _kakao_xlsx_bytes())
    assert kind == "카카오뱅크"
    assert len(df) == 2
    assert set(df["출처"]) == {"카카오뱅크"}


def test_unknown_format_raises():
    try:
        app.parse_any_file("readme.txt", b"hello world this is not a statement")
        raise AssertionError("ValueError가 나야 함")
    except ValueError as e:
        assert "인식하지 못했어요" in str(e)


def test_detect_ignores_extension_lies():
    """확장자가 .xls여도 내용이 IBK HTML이면 IBK로 감지 (내용 우선)."""
    kind, _ = app.parse_any_file("아무이름.xls", IBK_HTML)
    assert kind == "IBK기업은행"


# ── find_bc_echo_rows ──────────────────────────────────
def _sheet_df(rows):
    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    df["금액"] = pd.to_numeric(df["금액"])
    return df


def test_echo_detects_matching_bc_check():
    df_new = pd.DataFrame([
        {"날짜": "2026-05-18", "출처": "IBK기업은행", "유형": "출금",
         "금액": 89000, "내역": "복덩이숯불갈비"},
        {"날짜": "2026-05-20", "출처": "IBK기업은행", "유형": "출금",
         "금액": 12000, "내역": "다른가게"},
    ])
    df_all = _sheet_df([
        {"날짜": "2026-05-18", "출처": "BC카드(체크)", "유형": "출금",
         "금액": 89000, "내역": "복덩숫불갈비", "카테고리": "식비", "원문": ""},
    ])
    mask = app.find_bc_echo_rows(df_new, df_all)
    assert mask.tolist() == [True, False]


def test_echo_ignores_non_ibk_and_income():
    df_new = pd.DataFrame([
        {"날짜": "2026-05-18", "출처": "현대카드", "유형": "출금",
         "금액": 89000, "내역": "같은금액이지만현대"},
        {"날짜": "2026-05-18", "출처": "IBK기업은행", "유형": "입금",
         "금액": 89000, "내역": "입금은제외"},
    ])
    df_all = _sheet_df([
        {"날짜": "2026-05-18", "출처": "BC카드(체크)", "유형": "출금",
         "금액": 89000, "내역": "x", "카테고리": "기타", "원문": ""},
    ])
    mask = app.find_bc_echo_rows(df_new, df_all)
    assert mask.tolist() == [False, False]


def test_echo_empty_inputs():
    assert app.find_bc_echo_rows(pd.DataFrame(), pd.DataFrame()).empty
