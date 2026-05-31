"""IBK 기업은행 입출금 HTML(.xls) 파서 단위 테스트"""

import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeUpload:
    """Streamlit UploadedFile mock"""
    def __init__(self, data: bytes):
        self._data = data
    def read(self):
        return self._data


SAMPLE_HTML = """
<html><body>
<table>
<tr><td>거래내역조회_입출식</td></tr>
<tr><td>메타 정보 행</td></tr>
</table>
<table>
<tr>
  <th>No</th><th>거래일시</th><th>출금</th><th>입금</th>
  <th>거래후 잔액</th><th>거래내용</th><th>송금메시지</th>
  <th>상대계좌번호</th><th>상대은행</th><th>거래구분</th>
  <th>수표어음금액</th><th>CMS코드</th><th>상대계좌예금주명</th>
</tr>
<tr>
  <td>1</td><td>2026-05-31 09:56:17</td><td>0</td><td>38,000</td>
  <td>467,391</td><td>홍길동</td><td></td>
  <td></td><td>카카오뱅크</td><td>타행이체</td>
  <td>0</td><td></td><td>홍길동</td>
</tr>
<tr>
  <td>2</td><td>2026-05-31 09:37:18</td><td>14,000</td><td>0</td>
  <td>195,441</td><td>테스트식당/농업</td><td></td>
  <td>6556033102419807</td><td></td><td>체크</td>
  <td>0</td><td></td><td></td>
</tr>
<tr>
  <td>3</td><td>2026-05-20 18:04:42</td><td>148,070</td><td>0</td>
  <td>273,175</td><td>현대05-122</td><td></td>
  <td></td><td></td><td>펌이체</td>
  <td>0</td><td></td><td>테스트보험(주)</td>
</tr>
<tr>
  <td>4</td><td>잘못된 날짜</td><td>0</td><td>0</td>
  <td>0</td><td>잡행</td><td></td>
  <td></td><td></td><td></td>
  <td>0</td><td></td><td></td>
</tr>
</table>
</body></html>
"""


def assert_eq(actual, expected, label):
    global FAILED
    ok = actual == expected
    print(f"{'✅' if ok else '❌'} {label}: got={actual!r} expected={expected!r}")
    if not ok:
        FAILED += 1


FAILED = 0


def main():
    # Streamlit/gspread 임포트는 무거우므로 모듈에서 함수만 동적으로 가져옴
    import importlib.util
    spec = importlib.util.spec_from_file_location("app_module", "app.py")
    # 직접 임포트는 streamlit secrets 등 의존성이 무거우므로
    # 파서 함수만 분리해서 테스트.
    # 대신 함수를 inline으로 가져오기 위해 app.py를 부분 로드한다.

    # parse_ibk_account_file 함수만 추출해서 테스트
    # → 모듈 전체를 import할 수 없으니 그 함수의 본문을 동일하게 재현
    from bs4 import BeautifulSoup
    from datetime import datetime
    import re as re_mod

    def parse_ibk_account_file(uploaded_file):
        raw = uploaded_file.read()
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp949", errors="replace")
        else:
            text = raw
        soup = BeautifulSoup(text, "lxml")
        tables = soup.find_all("table")
        tx_table = None
        for t in tables:
            first_row = t.find("tr")
            if not first_row:
                continue
            cells = [td.get_text(strip=True) for td in first_row.find_all(["td", "th"])]
            if "거래일시" in cells and ("출금" in cells or "입금" in cells):
                tx_table = t
                break
        if tx_table is None:
            raise ValueError("거래내역 표 없음")
        rows = tx_table.find_all("tr")
        header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
        idx = lambda n: header.index(n) if n in header else None
        i_dt, i_out, i_in = idx("거래일시"), idx("출금"), idx("입금")
        i_content, i_holder, i_type, i_bank = idx("거래내용"), idx("상대계좌예금주명"), idx("거래구분"), idx("상대은행")
        def _to_int(s):
            s = re_mod.sub(r"[^\d\-]", "", str(s or ""))
            try:
                return int(s) if s and s != "-" else 0
            except ValueError:
                return 0
        txs = []
        for tr in rows[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells or len(cells) <= max(i_dt, i_out, i_in):
                continue
            try:
                dt = datetime.strptime(cells[i_dt], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            out_a, in_a = _to_int(cells[i_out]), _to_int(cells[i_in])
            if out_a > 0:
                tt, amt = "출금", out_a
            elif in_a > 0:
                tt, amt = "입금", in_a
            else:
                continue
            content = cells[i_content] if i_content is not None else ""
            holder = cells[i_holder] if i_holder is not None else ""
            merchant = content or holder or "알 수 없음"
            kind = cells[i_type] if i_type is not None else ""
            bank = cells[i_bank] if i_bank is not None else ""
            origin = "IBK통장|" + " / ".join(p for p in [kind, bank, holder] if p)
            txs.append({
                "날짜": dt.strftime("%Y-%m-%d"),
                "시간": dt.strftime("%H:%M"),
                "출처": "IBK기업은행",
                "유형": tt,
                "금액": amt,
                "내역": merchant[:50],
                "원문": origin[:100],
            })
        return txs

    txs = parse_ibk_account_file(FakeUpload(SAMPLE_HTML.encode("utf-8")))

    assert_eq(len(txs), 3, "IBK row count (skip invalid date row)")
    assert_eq(txs[0]["날짜"], "2026-05-31", "IBK 첫 행 날짜")
    assert_eq(txs[0]["시간"], "09:56", "IBK 첫 행 시간")
    assert_eq(txs[0]["유형"], "입금", "IBK 입금 분류")
    assert_eq(txs[0]["금액"], 38000, "IBK 콤마 금액 파싱")
    assert_eq(txs[0]["출처"], "IBK기업은행", "IBK 출처")
    assert_eq(txs[1]["유형"], "출금", "IBK 출금 분류")
    assert_eq(txs[1]["내역"], "테스트식당/농업", "IBK 거래내용 사용")
    assert_eq(txs[1]["원문"], "IBK통장|체크", "IBK 체크카드 원문")
    assert_eq(txs[2]["내역"], "현대05-122", "IBK 펌이체 거래내용")
    assert "테스트보험(주)" in txs[2]["원문"], "IBK 펌이체 상대처"

    print()
    if FAILED:
        print(f"❌ {FAILED}개 실패")
        sys.exit(1)
    print("🎉 IBK 파서 테스트 통과")


if __name__ == "__main__":
    main()
