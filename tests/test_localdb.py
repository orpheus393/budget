"""localdb (gspread 호환 SQLite 어댑터) 회귀 테스트.

app.py·email_parser가 실제로 쓰는 gspread API 표면 전체를 검증:
get_all_values/records, append_row(s), update_cell, update("A1", grid),
batch_update(단일 셀), delete_rows, clear, row_values, WorksheetNotFound,
ws.spreadsheet 역참조.
"""

import os
import sys

import gspread
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from localdb import open_workbook  # noqa: E402


@pytest.fixture
def wb(tmp_path):
    return open_workbook(str(tmp_path / "test.db"))


def test_worksheet_not_found_is_gspread_compatible(wb):
    with pytest.raises(gspread.WorksheetNotFound):
        wb.worksheet("없는시트")


def test_add_and_get_worksheet(wb):
    ws = wb.add_worksheet("거래내역", rows=10, cols=10)
    assert ws.title == "거래내역"
    assert ws.spreadsheet is wb          # app.py: ws_main.spreadsheet 사용
    assert wb.worksheet("거래내역").get_all_values() == []


def test_append_and_read_values_as_str(wb):
    ws = wb.add_worksheet("거래내역")
    ws.append_row(["날짜", "금액"])
    ws.append_rows([["2026-07-01", 15000], ["2026-07-02", None]])
    vals = ws.get_all_values()
    assert vals == [["날짜", "금액"], ["2026-07-01", "15000"], ["2026-07-02", ""]]
    assert ws.row_values(1) == ["날짜", "금액"]


def test_get_all_records_pads_short_rows(wb):
    ws = wb.add_worksheet("t")
    ws.append_row(["a", "b", "c"])
    ws.append_row(["1"])  # 짧은 행
    recs = ws.get_all_records()
    assert recs == [{"a": "1", "b": "", "c": ""}]


def test_update_cell_extends_row(wb):
    """스키마 마이그레이션 패턴: update_cell(1, len(header)+1, '입력경로')"""
    ws = wb.add_worksheet("t")
    ws.append_row(["날짜", "금액"])
    ws.update_cell(1, 3, "입력경로")
    assert ws.row_values(1) == ["날짜", "금액", "입력경로"]


def test_batch_update_single_cells(wb):
    """patch_sheet 패턴: [{'range': 'G12', 'values': [['여행/항공']]}]"""
    ws = wb.add_worksheet("t")
    ws.append_rows([["h"], ["r2"], ["r3"]])
    ws.batch_update(
        [{"range": "B2", "values": [["x"]]}, {"range": "B3", "values": [["y"]]}],
        value_input_option="USER_ENTERED",
    )
    assert ws.get_all_values()[1] == ["r2", "x"]
    assert ws.get_all_values()[2] == ["r3", "y"]


def test_update_a1_grid(wb):
    """save_budget 패턴: clear() 후 update('A1', rows)"""
    ws = wb.add_worksheet("예산")
    ws.append_rows([["old", "junk"]])
    ws.clear()
    ws.update("A1", [["카테고리", "월 예산"], ["식비", 600000]],
              value_input_option="USER_ENTERED")
    assert ws.get_all_records() == [{"카테고리": "식비", "월 예산": "600000"}]


def test_update_row_range(wb):
    """보금자리론 패턴: update('A3:G3', [[...]])"""
    ws = wb.add_worksheet("보금자리론")
    ws.append_rows([["h"] * 7, ["a"] * 7, ["b"] * 7])
    ws.update("A3:G3", [["1", "2", "3", "4", "5", "6", "7"]])
    assert ws.get_all_values()[2] == ["1", "2", "3", "4", "5", "6", "7"]


def test_delete_rows_reverse_order(wb):
    """clean_sheet/dedup 패턴: 뒤에서부터 delete_rows(n)"""
    ws = wb.add_worksheet("t")
    ws.append_rows([["h"], ["r2"], ["r3"], ["r4"]])
    for n in (4, 2):
        ws.delete_rows(n)
    assert [r[0] for r in ws.get_all_values()] == ["h", "r3"]


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "p.db")
    ws = open_workbook(path).add_worksheet("거래내역")
    ws.append_row(["영속성"])
    ws2 = open_workbook(path).worksheet("거래내역")
    assert ws2.get_all_values() == [["영속성"]]


def test_email_parser_saves_to_sqlite(tmp_path, monkeypatch):
    """save_to_sheets 통합: STORAGE=sqlite → 로컬 DB에 기록 + 중복 제외 + 자동 태그."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import email_parser

    db = str(tmp_path / "budget.db")
    monkeypatch.setenv("STORAGE", "sqlite")
    monkeypatch.setenv("DB_PATH", db)

    tx = {"날짜": "2026-07-01", "시간": "12:00", "출처": "카카오뱅크",
          "유형": "출금", "금액": 15000, "내역": "식당", "카테고리": "식비",
          "원문": "카카오뱅크 | 체크카드", "잔액": 985000}
    email_parser.save_to_sheets([tx])
    email_parser.save_to_sheets([tx])  # 같은 거래 재저장 → 중복 제외

    rows = open_workbook(db).worksheet("거래내역").get_all_values()
    assert len(rows) == 2  # 헤더 + 1행 (중복 안 쌓임)
    assert rows[1][9] == "자동:카카오뱅크"  # 입력경로 명시 태그
