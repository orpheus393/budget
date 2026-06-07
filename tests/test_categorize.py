"""guess_category와 learn_category_overrides 회귀 테스트."""

import app


def test_personal_remittance_default():
    # 일반 송금 키워드 (개인송금)
    assert app.guess_category("윤태수", "출금") == "개인송금"
    assert app.guess_category("정황섭", "입금") == "개인송금"


def test_salary_to_geunlosodeuk():
    # 급여 키워드 + 회사명 매칭
    assert app.guess_category("급여", "입금") == "근로소득"
    assert (
        app.guess_category("월급", "입금",
                            "IBK통장|인터넷 / 기업은행 / (주)라이징테크",
                            "IBK기업은행")
        == "근로소득"
    )


def test_card_debt_settlement():
    # 카드대금 자동이체 → 부채청산
    assert app.guess_category("현대카드01", "출금") == "부채청산"
    assert app.guess_category("비씨카드출금", "출금") == "부채청산"
    assert app.guess_category("KB카드출금", "출금") == "부채청산"


def test_lim_youngjae_branch_ibk():
    # IBK 출처 + 원문 농협 → 어머니
    assert (
        app.guess_category("임영재", "출금",
                            "IBK통장|스마트뱅킹 / 농협은행 / 임영재",
                            "IBK기업은행")
        == "어머니차입금"
    )
    # IBK 출처 + 원문 카카오뱅크 → 자기이체
    assert (
        app.guess_category("임영재", "출금",
                            "IBK통장|스마트뱅킹 / 카카오뱅크 / 임영재",
                            "IBK기업은행")
        == "자기이체"
    )
    # IBK 출처 + 원문 토스뱅크 → 자기이체
    assert (
        app.guess_category("임영재", "입금",
                            "IBK통장|타행이체 / 토스뱅크 / 임영재",
                            "IBK기업은행")
        == "자기이체"
    )


def test_lim_youngjae_branch_kakao():
    # 카뱅 출처 + 거래구분 계좌간자동이체 → 자기이체
    assert (
        app.guess_category("임영재", "출금",
                            "카카오뱅크 | 계좌간자동이체", "카카오뱅크")
        == "자기이체"
    )
    # 카뱅 출처 + 일반이체 → 어머니 (보수)
    assert (
        app.guess_category("임영재", "출금",
                            "카카오뱅크 | 일반이체", "카카오뱅크")
        == "어머니차입금"
    )
    assert (
        app.guess_category("임영재", "입금",
                            "카카오뱅크 | 일반입금", "카카오뱅크")
        == "어머니차입금"
    )


def test_housing_loan():
    assert app.guess_category("주택금융공사", "출금") == "주거/대출"


def test_travel():
    assert app.guess_category("환전출금", "출금") == "여행/항공"
    assert app.guess_category("외국통화", "출금") == "여행/항공"


def test_refund_input():
    # 환불 입금
    assert app.guess_category("비엣젯 환불", "입금") == "환불/캐시백"


def test_unknown_falls_back():
    # 매칭 안 되는 출금 → 기타
    assert app.guess_category("아이딜컨스트럭션", "출금") == "기타"
    # 매칭 안 되는 입금 → 수입
    assert app.guess_category("알수없는입금자", "입금") == "수입"


def test_overrides_take_precedence():
    """학습 매핑이 키워드 자동 분류보다 우선."""
    overrides = {"아이딜컨스트럭션": "식비"}
    assert app.guess_category("아이딜컨스트럭션", "출금",
                               overrides=overrides) == "식비"
    # override가 없으면 기존 자동 분류
    assert app.guess_category("아이딜컨스트럭션", "출금") == "기타"


def test_learn_overrides_basic():
    header = ["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문", "잔액"]
    # 아이딜컨스트럭션 3회 모두 '식비' (자동분류는 '기타'이므로 학습됨)
    rows = [
        ["2026-01-01", "", "현대카드", "출금", "5000", "아이딜컨스트럭션", "식비", "", ""],
        ["2026-01-02", "", "현대카드", "출금", "5000", "아이딜컨스트럭션", "식비", "", ""],
        ["2026-01-03", "", "현대카드", "출금", "5000", "아이딜컨스트럭션", "식비", "", ""],
    ]
    ov = app.learn_category_overrides(rows, header)
    assert ov == {"아이딜컨스트럭션": "식비"}


def test_learn_overrides_excludes_auto_matched():
    """자동 분류와 일치하는 매핑은 학습에서 제외 — 불필요하므로."""
    header = ["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문", "잔액"]
    rows = [
        ["2026-01-01", "", "현대카드", "출금", "5000", "스타벅스코엑스", "식비", "", ""],
        ["2026-01-02", "", "현대카드", "출금", "5000", "스타벅스코엑스", "식비", "", ""],
    ]
    ov = app.learn_category_overrides(rows, header)
    # 스타벅스는 키워드 자동분류로도 '식비'이므로 override 등록 X
    assert "스타벅스코엑스" not in ov


def test_learn_overrides_min_count():
    """min_count(=2) 미만은 학습되지 않음."""
    header = ["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문", "잔액"]
    rows = [
        ["2026-01-01", "", "현대카드", "출금", "5000", "한번만나오는가맹점", "식비", "", ""],
    ]
    ov = app.learn_category_overrides(rows, header)
    assert "한번만나오는가맹점" not in ov


def test_learn_overrides_confidence():
    """confidence(80%) 미만이면 학습되지 않음 — 분포가 한 카테고리에 쏠려야 함."""
    header = ["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문", "잔액"]
    # 50/50 분포 — confidence 50%로 80% 미달
    rows = [
        ["2026-01-01", "", "현대카드", "출금", "5000", "엇갈리는가맹점", "식비", "", ""],
        ["2026-01-02", "", "현대카드", "출금", "5000", "엇갈리는가맹점", "쇼핑", "", ""],
    ]
    ov = app.learn_category_overrides(rows, header)
    assert "엇갈리는가맹점" not in ov
