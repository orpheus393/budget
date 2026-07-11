"""로컬 SQLite 스토리지 — gspread 호환 어댑터.

Google Sheets 대신 PC의 SQLite 파일 하나(data/budget.db)에 저장하면서,
app.py·email_parser.py가 쓰는 gspread API 표면을 그대로 흉내낸다:

    get_all_values / get_all_records / row_values / append_row / append_rows /
    update_cell / update / batch_update / delete_rows / clear /
    workbook.worksheet / add_worksheet / ws.spreadsheet

덕분에 기존 2,900줄 로직은 한 줄도 안 바꾸고, 백엔드 선택부만 갈아끼운다.
워크시트당 행이 수천 개 수준인 가계부 규모에서는 JSON 블롭 저장이 가장
단순하고 충분히 빠르다 (관계형 스키마가 필요해지면 이 파일만 교체).

동시성: 단일 사용자 로컬 전제. 매 연산마다 connect→commit→close.
"""

import json
import os
import re
import sqlite3

import gspread  # WorksheetNotFound 예외를 기존 except 절과 호환시키기 위함


def _resolve_worksheet_not_found():
    """gspread.WorksheetNotFound를 그대로 쓰되, 테스트처럼 gspread가
    mock인 환경에서는 진짜 예외 클래스로 교체해 raise/except 양쪽 호환."""
    exc = getattr(gspread, "WorksheetNotFound", None)
    if isinstance(exc, type) and issubclass(exc, BaseException):
        return exc

    class WorksheetNotFound(KeyError):
        pass

    gspread.WorksheetNotFound = WorksheetNotFound
    return WorksheetNotFound


WorksheetNotFound = _resolve_worksheet_not_found()

_A1_RE = re.compile(r"^([A-Za-z]+)(\d+)")


def _col_letters_to_idx(letters: str) -> int:
    """'A'→1, 'Z'→26, 'AA'→27"""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _parse_a1(cell: str) -> tuple:
    """'G12' 또는 'A5:G5' → 시작 셀 (row, col) 1-base."""
    m = _A1_RE.match(cell.strip())
    if not m:
        raise ValueError(f"A1 표기 해석 실패: {cell!r}")
    return int(m.group(2)), _col_letters_to_idx(m.group(1))


def _to_cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


class LocalWorksheet:
    def __init__(self, workbook: "LocalWorkbook", name: str):
        self.spreadsheet = workbook  # gspread ws.spreadsheet 호환
        self.title = name
        self._wb = workbook
        self._name = name

    # ── 읽기 ─────────────────────────────────────────
    def get_all_values(self) -> list:
        return [[_to_cell_str(c) for c in row] for row in self._wb._read(self._name)]

    def row_values(self, row_idx: int) -> list:
        rows = self.get_all_values()
        return rows[row_idx - 1] if 0 < row_idx <= len(rows) else []

    def get_all_records(self) -> list:
        rows = self.get_all_values()
        if len(rows) < 2:
            return []
        header = rows[0]
        out = []
        for row in rows[1:]:
            padded = row + [""] * (len(header) - len(row))
            out.append(dict(zip(header, padded)))
        return out

    # ── 쓰기 ─────────────────────────────────────────
    def append_row(self, values, value_input_option=None):
        rows = self._wb._read(self._name)
        rows.append(list(values))
        self._wb._write(self._name, rows)

    def append_rows(self, new_rows, value_input_option=None):
        rows = self._wb._read(self._name)
        rows.extend([list(r) for r in new_rows])
        self._wb._write(self._name, rows)

    def update_cell(self, row_idx: int, col_idx: int, value):
        rows = self._wb._read(self._name)
        while len(rows) < row_idx:
            rows.append([])
        row = rows[row_idx - 1]
        while len(row) < col_idx:
            row.append("")
        row[col_idx - 1] = value
        self._wb._write(self._name, rows)

    def _write_grid(self, start_row: int, start_col: int, values):
        rows = self._wb._read(self._name)
        for dr, vrow in enumerate(values):
            r = start_row + dr
            while len(rows) < r:
                rows.append([])
            row = rows[r - 1]
            need = start_col - 1 + len(vrow)
            while len(row) < need:
                row.append("")
            for dc, v in enumerate(vrow):
                row[start_col - 1 + dc] = v
        self._wb._write(self._name, rows)

    def update(self, range_name, values=None, value_input_option=None):
        """gspread v5 스타일 update('A1', rows) — 앱은 이 형태만 사용."""
        if values is None and isinstance(range_name, list):
            # gspread v6 스타일 update(values) 방어
            range_name, values = "A1", range_name
        start_row, start_col = _parse_a1(str(range_name))
        self._write_grid(start_row, start_col, values or [])

    def batch_update(self, requests, value_input_option=None):
        """[{'range': 'G12', 'values': [[v]]}] — 앱은 단일 셀/행 범위만 사용."""
        for req in requests:
            start_row, start_col = _parse_a1(str(req["range"]))
            self._write_grid(start_row, start_col, req.get("values") or [])

    def delete_rows(self, start_index: int, end_index: int = None):
        rows = self._wb._read(self._name)
        end = end_index if end_index is not None else start_index
        del rows[start_index - 1:end]
        self._wb._write(self._name, rows)

    def clear(self):
        self._wb._write(self._name, [])


class LocalWorkbook:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._conn() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS worksheets ("
                " name TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )

    def _conn(self):
        return sqlite3.connect(self.path)

    # gspread Spreadsheet 호환 표면
    @property
    def title(self) -> str:
        return os.path.basename(self.path)

    @property
    def url(self) -> str:
        return f"sqlite://{os.path.abspath(self.path)}"

    def worksheet(self, name: str) -> LocalWorksheet:
        with self._conn() as con:
            row = con.execute(
                "SELECT 1 FROM worksheets WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise WorksheetNotFound(name)
        return LocalWorksheet(self, name)

    def add_worksheet(self, title: str, rows: int = 100, cols: int = 20) -> LocalWorksheet:
        with self._conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO worksheets (name, data) VALUES (?, ?)",
                (title, "[]"),
            )
        return LocalWorksheet(self, title)

    # 내부 저장
    def _read(self, name: str) -> list:
        with self._conn() as con:
            row = con.execute(
                "SELECT data FROM worksheets WHERE name = ?", (name,)
            ).fetchone()
        return json.loads(row[0]) if row else []

    def _write(self, name: str, rows: list):
        with self._conn() as con:
            con.execute(
                "INSERT INTO worksheets (name, data) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET data = excluded.data",
                (name, json.dumps(rows, ensure_ascii=False, default=str)),
            )


def open_workbook(path: str = "data/budget.db") -> LocalWorkbook:
    return LocalWorkbook(path)
