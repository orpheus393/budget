"""detect_outliers, forecast_cash_flow, _net_worth_snapshot, _month_pnl 회귀."""

import pandas as pd

import app


def _build_df(rows):
    """list[dict] → DataFrame with proper types."""
    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    df["금액"] = pd.to_numeric(df["금액"])
    if "잔액" in df.columns:
        df["잔액"] = pd.to_numeric(df["잔액"], errors="coerce")
    return df


def test_month_pnl_excludes_non_pnl():
    df = _build_df([
        {"날짜": "2026-01-15", "출처": "IBK기업은행", "유형": "입금",
         "금액": 5000000, "내역": "급여", "카테고리": "근로소득", "원문": ""},
        {"날짜": "2026-01-15", "출처": "IBK기업은행", "유형": "출금",
         "금액": 1500000, "내역": "현대카드01", "카테고리": "부채청산", "원문": ""},
        {"날짜": "2026-01-20", "출처": "현대카드", "유형": "출금",
         "금액": 100000, "내역": "스타벅스코엑스", "카테고리": "식비", "원문": ""},
        {"날짜": "2026-01-25", "출처": "IBK기업은행", "유형": "출금",
         "금액": 500000, "내역": "임영재", "카테고리": "어머니차입금", "원문": ""},
    ])
    inc, exp, bal, sal, fix, var = app._month_pnl(df, 2026, 1)
    # 부채청산·어머니차입금은 제외
    assert inc == 5000000
    assert exp == 100000
    assert bal == 4900000
    assert sal == 5000000
    # 식비는 변동비
    assert fix == 0
    assert var == 100000


def test_month_pnl_fixed_vs_variable():
    df = _build_df([
        {"날짜": "2026-01-01", "출처": "IBK", "유형": "출금",
         "금액": 800000, "내역": "주택금융공사", "카테고리": "주거/대출", "원문": ""},
        {"날짜": "2026-01-02", "출처": "IBK", "유형": "출금",
         "금액": 100000, "내역": "LG U+", "카테고리": "통신", "원문": ""},
        {"날짜": "2026-01-03", "출처": "카드", "유형": "출금",
         "금액": 30000, "내역": "넷플릭스", "카테고리": "구독", "원문": ""},
        {"날짜": "2026-01-04", "출처": "카드", "유형": "출금",
         "금액": 50000, "내역": "스타벅스", "카테고리": "식비", "원문": ""},
    ])
    inc, exp, bal, sal, fix, var = app._month_pnl(df, 2026, 1)
    assert fix == 800000 + 100000 + 30000  # 주거/대출 + 통신 + 구독
    assert var == 50000  # 식비


def test_net_worth_snapshot():
    df = _build_df([
        {"날짜": "2026-05-30", "출처": "카카오뱅크", "유형": "출금", "금액": 100,
         "내역": "x", "카테고리": "식비", "원문": "", "잔액": -10000000},
        {"날짜": "2026-05-31", "출처": "카카오뱅크", "유형": "출금", "금액": 50,
         "내역": "y", "카테고리": "식비", "원문": "", "잔액": -10500000},
        {"날짜": "2026-05-31", "출처": "IBK기업은행", "유형": "입금", "금액": 500,
         "내역": "z", "카테고리": "수입", "원문": "", "잔액": 2000000},
        # 어머니 거래
        {"날짜": "2026-04-15", "출처": "IBK기업은행", "유형": "입금", "금액": 1000000,
         "내역": "임영재", "카테고리": "어머니차입금", "원문": "", "잔액": None},
        {"날짜": "2026-05-01", "출처": "IBK기업은행", "유형": "출금", "금액": 300000,
         "내역": "임영재", "카테고리": "어머니차입금", "원문": "", "잔액": None},
        # 카드 사용
        {"날짜": "2026-05-10", "출처": "현대카드", "유형": "출금", "금액": 200000,
         "내역": "스타벅스", "카테고리": "식비", "원문": "", "잔액": None},
    ])
    snap = app._net_worth_snapshot(df, 2026, 5)
    assert snap["kakao"] == -10500000  # 가장 최근 카뱅 잔액
    assert snap["ibk"] == 2000000      # 가장 최근 IBK 잔액
    assert snap["mom_debt"] == 700000  # 1,000,000 빌림 − 300,000 갚음
    assert snap["card_debt"] == 200000  # 5월 카드 사용
    expected_nw = (-10500000 + 2000000) - 700000 - 200000
    assert snap["net_worth"] == expected_nw


def test_detect_outliers_basic():
    # 식비 평소 1만원, 한 건이 10만원 → 10배 → 이상치
    df = _build_df([
        {"날짜": f"2026-01-{d:02d}", "출처": "현대카드", "유형": "출금",
         "금액": 10000, "내역": "편의점", "카테고리": "식비", "원문": ""}
        for d in range(1, 11)
    ])
    df = pd.concat([df, _build_df([
        {"날짜": "2026-01-15", "출처": "현대카드", "유형": "출금",
         "금액": 100000, "내역": "고급식당", "카테고리": "식비", "원문": ""},
    ])])
    out = app.detect_outliers(df, 2026, 1, threshold_ratio=3.0)
    # 10만원 거래가 잡혀야 함
    assert not out.empty
    assert (out["금액"] == 100000).any()


def test_detect_outliers_skips_small_history():
    """min_history(=5) 미만 카테고리는 이상치 검사 안 함."""
    df = _build_df([
        {"날짜": "2026-01-01", "출처": "X", "유형": "출금", "금액": 1000,
         "내역": "a", "카테고리": "쇼핑", "원문": ""},
        {"날짜": "2026-01-02", "출처": "X", "유형": "출금", "금액": 1000000,
         "내역": "b", "카테고리": "쇼핑", "원문": ""},
    ])
    out = app.detect_outliers(df, 2026, 1, threshold_ratio=3.0)
    assert out.empty  # 이력 2건이라 min_history=5 미달


def test_forecast_cash_flow_basic():
    # 3개월 각 100만 수입, 80만 지출 → 다음 3개월 손익 +20만/월
    rows = []
    for m in range(1, 4):
        rows.append({"날짜": f"2026-{m:02d}-15", "출처": "IBK", "유형": "입금",
                     "금액": 1000000, "내역": "급여", "카테고리": "근로소득", "원문": ""})
        rows.append({"날짜": f"2026-{m:02d}-20", "출처": "현대카드", "유형": "출금",
                     "금액": 800000, "내역": "지출", "카테고리": "쇼핑", "원문": ""})
    df = _build_df(rows)
    fc = app.forecast_cash_flow(df, months_ahead=3, history_months=3)
    assert len(fc) == 3
    assert (fc["예상손익"] == 200000).all()
    # 누적: 20, 40, 60만
    assert list(fc["누적손익"]) == [200000, 400000, 600000]


def test_forecast_cash_flow_empty():
    fc = app.forecast_cash_flow(pd.DataFrame(), months_ahead=3)
    assert fc.empty


def test_normalize_korean_date():
    assert app._normalize_korean_date("2026년 05월 30일") == "2026-05-30"
    assert app._normalize_korean_date("2026.05.30") == "2026-05-30"
    assert app._normalize_korean_date("2026-05-30") == "2026-05-30"
    assert app._normalize_korean_date("2026/5/3") == "2026-05-03"
    assert app._normalize_korean_date("") == ""


def test_delta_helper():
    assert app._delta(110, 100) == "+10원 (+10.0%)"
    assert app._delta(90, 100) == "-10원 (-10.0%)"
    assert app._delta(100, 0) is None


def test_generate_annual_report_basic():
    df = _build_df([
        {"날짜": "2026-01-15", "출처": "IBK", "유형": "입금", "금액": 5000000,
         "내역": "급여", "카테고리": "근로소득", "원문": "", "잔액": 2000000},
        {"날짜": "2026-01-20", "출처": "현대카드", "유형": "출금", "금액": 100000,
         "내역": "스타벅스", "카테고리": "식비", "원문": "", "잔액": None},
        {"날짜": "2026-02-15", "출처": "IBK", "유형": "입금", "금액": 5000000,
         "내역": "급여", "카테고리": "근로소득", "원문": "", "잔액": 2500000},
    ])
    md = app.generate_annual_report(df, 2026)
    assert "2026년 가계부 결산" in md
    assert "10,000,000" in md  # 근로소득 5M × 2
    assert "100,000" in md     # 식비
    assert "98.0%" in md or "99.0%" in md or "저축률" in md
    assert "스타벅스" in md  # 가맹점 TOP에 등장


def test_generate_annual_report_empty_year():
    df = _build_df([
        {"날짜": "2025-01-15", "출처": "IBK", "유형": "입금", "금액": 1000,
         "내역": "x", "카테고리": "수입", "원문": "", "잔액": None},
    ])
    md = app.generate_annual_report(df, 2026)
    assert "2026년" in md
    assert "거래 내역이 없습니다" in md


def test_build_notification_text_basic():
    df = _build_df([
        {"날짜": "2026-05-15", "출처": "IBK", "유형": "입금", "금액": 5000000,
         "내역": "급여", "카테고리": "근로소득", "원문": "", "잔액": 1000000},
        {"날짜": "2026-05-20", "출처": "현대카드", "유형": "출금", "금액": 100000,
         "내역": "스타벅스", "카테고리": "식비", "원문": "", "잔액": None},
    ])
    txt = app.build_notification_text(df, 2026, 5)
    assert "2026년 05월" in txt
    assert "수입 5,000,000" in txt
    assert "지출 100,000" in txt
    assert "저축률" in txt


def test_send_slack_notification_no_url():
    """webhook URL 없으면 False 반환."""
    assert app.send_slack_notification("test") is False
    assert app.send_slack_notification("test", webhook_url="") is False
