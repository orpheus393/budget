"""sync_sheet_to_local — 클라우드가 시트에만 남긴 거래를 로컬로 회수.

로컬 모드 전환 후 클라우드 cron이 먼저 처리한 거래가 시트에만 남는
'분산' 상황의 회귀 테스트. 중복 키는 email_parser.save_to_sheets와 동일.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sync_sheet_to_local import missing_rows, normalize, row_key  # noqa: E402

HEADER = ["날짜", "시간", "출처", "유형", "금액", "내역",
          "카테고리", "원문", "잔액", "입력경로"]


def _row(date, source, amount, merchant, path=""):
    return [date, "12:00", source, "출금", str(amount), merchant,
            "기타", f"{source} 원문", "", path]


def test_returns_only_rows_missing_locally():
    sheet = [HEADER,
             _row("2026-07-20", "BC카드", 5000, "가맹점A"),
             _row("2026-08-19", "BC카드", 7000, "가맹점B")]
    local = [HEADER, _row("2026-07-20", "BC카드", 5000, "가맹점A")]
    out = missing_rows(sheet, local)
    assert len(out) == 1
    assert out[0][0] == "2026-08-19"


def test_since_filter_limits_range():
    sheet = [HEADER,
             _row("2026-06-01", "KB카드", 1000, "옛날"),
             _row("2026-08-19", "KB카드", 7000, "최근")]
    out = missing_rows(sheet, [HEADER], since="2026-07-29")
    assert [r[0] for r in out] == ["2026-08-19"]


def test_duplicate_rows_within_sheet_collapse():
    dup = _row("2026-08-19", "BC카드", 7000, "가맹점B")
    out = missing_rows([HEADER, dup, list(dup)], [HEADER])
    assert len(out) == 1


def test_empty_input_path_filled_with_source():
    out = missing_rows([HEADER, _row("2026-08-19", "BC카드", 7000, "가맹점B")],
                       [HEADER])
    assert out[0][9] == "자동:BC카드"


def test_existing_input_path_preserved():
    out = missing_rows(
        [HEADER, _row("2026-08-19", "IBK통장", 7000, "가맹점B", "수동:IBK통장")],
        [HEADER])
    assert out[0][9] == "수동:IBK통장"


def test_short_rows_padded_to_ten_columns():
    out = missing_rows([HEADER, ["2026-08-19", "12:00", "BC카드", "출금",
                                 "7000", "가맹점B"]], [HEADER])
    assert len(out[0]) == 10


def test_row_key_matches_save_to_sheets_format():
    key = row_key(_row("2026-08-19", "BC카드", 7000, "가맹점B"))
    assert key == "2026-08-19_BC카드_7000_가맹점B"


def test_blank_rows_ignored():
    assert missing_rows([HEADER, [], ["", "", ""]], [HEADER]) == []


def test_normalize_truncates_extra_columns():
    assert len(normalize(_row("2026-08-19", "BC카드", 1, "X") + ["잉여"])) == 10
