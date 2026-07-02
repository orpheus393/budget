"""classify_input_path / _input_path_breakdown 회귀 테스트.

각 행이 자동(cron 이메일 파싱) vs 수동(Excel 업로드)으로 들어왔는지
'원문' prefix로 추론하는 로직, 그리고 자동↔수동 중복 감지를 검증.
"""

import pandas as pd

import app


def _build_df(rows):
    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    df["금액"] = pd.to_numeric(df["금액"])
    return df


# ── 명시 태그(입력경로 컬럼) 우선 ─────────────────────
def test_explicit_auto_tag_wins():
    # 원문·출처가 수동을 가리켜도 명시 태그가 자동이면 자동
    assert app.classify_input_path("현대카드 | X", "현대카드", "자동:BC카드(신용)") == "자동"


def test_explicit_manual_tag_wins():
    assert app.classify_input_path("BC카드 월간명세서", "BC카드(신용)", "수동:현대카드") == "수동"


def test_explicit_empty_falls_back_to_origin():
    assert app.classify_input_path("현대카드 | X", "현대카드", "") == "수동"


# ── classify_input_path: 원문 prefix 기반 ──────────────
def test_manual_hyundai_by_origin():
    assert app.classify_input_path("현대카드 | 스타벅스", "현대카드") == "수동"


def test_manual_ibk_by_origin():
    assert app.classify_input_path("IBK통장|체크 / 농협 / 임영재", "IBK기업은행") == "수동"


def test_manual_kakao_by_origin():
    assert app.classify_input_path("카카오뱅크 | 일반이체 | 월세", "카카오뱅크") == "수동"


def test_auto_bc_statement_by_origin():
    assert app.classify_input_path("BC카드 월간명세서", "BC카드(신용)") == "자동"


# ── 출처 기반 fallback (원문이 비었을 때) ──────────────
def test_auto_fallback_by_source_kb():
    assert app.classify_input_path("", "KB카드") == "자동"


def test_auto_fallback_by_source_bc_check():
    assert app.classify_input_path("", "BC카드(체크)") == "자동"


def test_manual_fallback_by_source_hyundai():
    assert app.classify_input_path("", "현대카드") == "수동"


def test_unknown_when_no_signal():
    assert app.classify_input_path("", "네이버페이") == "불명"


# ── origin prefix가 source fallback보다 우선 ───────────
def test_origin_prefix_wins_over_source():
    # 출처는 모호하지만 원문이 명백히 수동 업로드 prefix
    assert app.classify_input_path("카카오뱅크 | 이체", "") == "수동"


# ── _input_path_breakdown: 집계 + 중복 감지 ────────────
def test_breakdown_groups_by_source_and_path():
    df = _build_df([
        {"날짜": "2026-05-01", "출처": "현대카드", "유형": "출금", "금액": 10000,
         "내역": "스벅", "카테고리": "식비", "원문": "현대카드 | 스벅"},
        {"날짜": "2026-05-02", "출처": "현대카드", "유형": "출금", "금액": 20000,
         "내역": "김밥", "카테고리": "식비", "원문": "현대카드 | 김밥"},
        {"날짜": "2026-05-03", "출처": "BC카드(신용)", "유형": "출금", "금액": 30000,
         "내역": "마트", "카테고리": "기타", "원문": "BC카드 월간명세서"},
    ])
    rows, dups = app._input_path_breakdown(df)
    by = {(r["출처"], r["경로"]): r["행수"] for r in rows}
    assert by[("현대카드", "수동")] == 2
    assert by[("BC카드(신용)", "자동")] == 1
    assert dups == []


def test_breakdown_detects_auto_manual_duplicate():
    # 같은 거래(같은 날짜·금액·내역)가 자동(BC명세서)과 수동(IBK통장 echo) 양쪽에
    df = _build_df([
        {"날짜": "2026-05-18", "출처": "BC카드(체크)", "유형": "출금", "금액": 89000,
         "내역": "복덩숯불갈비", "카테고리": "식비", "원문": "BC카드 월간명세서"},
        {"날짜": "2026-05-18", "출처": "IBK기업은행", "유형": "출금", "금액": 89000,
         "내역": "복덩숯불갈비", "카테고리": "기타", "원문": "IBK통장|체크 / 복덩숯불갈비"},
    ])
    rows, dups = app._input_path_breakdown(df)
    assert len(dups) == 1
    assert dups[0]["금액"] == 89000
    assert "자동" in dups[0]["경로들"] and "수동" in dups[0]["경로들"]


def test_breakdown_no_false_duplicate_different_amount():
    df = _build_df([
        {"날짜": "2026-05-18", "출처": "BC카드(체크)", "유형": "출금", "금액": 89000,
         "내역": "복덩숯불갈비", "카테고리": "식비", "원문": "BC카드 월간명세서"},
        {"날짜": "2026-05-18", "출처": "IBK기업은행", "유형": "출금", "금액": 12000,
         "내역": "복덩숯불갈비", "카테고리": "기타", "원문": "IBK통장|체크 / 복덩숯불갈비"},
    ])
    rows, dups = app._input_path_breakdown(df)
    assert dups == []


def test_breakdown_uses_explicit_column():
    # 입력경로 컬럼이 있으면 원문·출처 추론보다 우선
    df = _build_df([
        {"날짜": "2026-05-01", "출처": "네이버페이", "유형": "입금", "금액": 5000,
         "내역": "환급", "카테고리": "수입", "원문": "네이버페이 알림",
         "입력경로": "자동:네이버페이"},
    ])
    rows, dups = app._input_path_breakdown(df)
    assert rows[0]["경로"] == "자동"  # 원문만이면 '불명'인데 명시 태그로 '자동'


def test_breakdown_empty_df():
    rows, dups = app._input_path_breakdown(pd.DataFrame())
    assert rows == [] and dups == []
