"""
app.py - 가계부 Streamlit 대시보드
"""

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re
import io
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date
import calendar


# ── 카테고리 자동 분류 ────────────────────────────────
# 손익(P&L) 집계에서 제외할 카테고리:
# - 어머니차입금: 본인 명의 농협 계좌(엄마 자금)와의 입출금. 부채 변동이지 수입/지출 아님
# - 부채청산: 카드대금 자동이체 등 — 카드 사용 행이 별도 출처로 잡히므로 이중집계 방지
# - 자기이체: 본인 계좌 간 이동 (IBK↔카뱅↔농협)
# - 환불/캐시백: 가맹점 환불·카드사 캐시백 — 명목 입금이나 수입은 아님
NON_PNL_CATEGORIES = {"어머니차입금", "부채청산", "자기이체", "환불/캐시백"}

# 고정비(매월 자동 발생, 통제 어려움) vs 변동비(통제 가능)
# 손익 지출 = 고정비 + 변동비
FIXED_CATEGORIES = {
    "주거/관리", "주거/대출", "통신", "구독", "보험/금융", "교육/자녀",
}

CARD_SOURCES = ("현대카드", "BC카드", "삼성카드", "KB카드", "신한카드", "롯데카드", "비씨카드")

# 입금/출금 양방향 매칭 카테고리 (부호와 무관하게 키워드만으로 분류)
# 어머니차입금/자기이체는 별도 처리(원문에 따라 분기) — guess_category 참조
BIDIRECTIONAL_RULES = [
    ("부채청산", ["현대카드", "비씨카드출금", "비씨카드결제", "KB카드출금",
                  "삼성카드출금", "카드결제대금", "카드대금"]),
]

# 출금 전용 분류
CATEGORY_KEYWORDS = {
    "식비": ["식당", "음식", "카페", "커피", "배달", "맥도날드", "스타벅스", "버거킹",
             "편의점", "GS25", "CU", "씨유", "세븐", "이마트24", "투썸", "메가", "공차",
             "BBQ", "교촌", "도미노", "피자", "스시", "돈가스", "치킨", "분식",
             "농산", "축산", "유통", "정육", "닭집", "바다", "난바다", "하나로마트",
             "요기요", "배민", "쿠팡이츠", "주식회사 우아한"],
    "교통": ["택시", "버스", "지하철", "주유", "카카오택시", "티머니", "하이패스",
             "S-OIL", "SK에너지", "GS칼텍스", "현대오일뱅크", "철도", "코레일",
             "고속도로", "경기마을", "내륙고속", "tmoney", "교통카드", "도로공사",
             "주차장", "아이파킹"],
    "여행/항공": ["항공", "AIR", "VIETJET", "비엣젯", "티웨이", "대한항공", "아시아나",
                  "제주항공", "진에어", "호텔", "리조트", "에어비앤비", "airbnb",
                  "BOOKING", "AGODA", "익스피디아", "EXPEDIA",
                  "환전", "외국통화", "Grab"],
    "쇼핑": ["쿠팡", "G마켓", "옥션", "11번가", "이마트", "홈플러스",
             "코스트코", "마켓컬리", "올리브영", "다이소", "무신사", "ALIEXPRESS",
             "롯데쇼핑", "롯데마트", "AMAZON", "당근", "지마켓", "네이버페이",
             "온누리충전", "온누리상품권"],
    "의료": ["병원", "약국", "의원", "클리닉", "치과", "한의원", "위즈헤어"],
    "통신": ["SKT", "KT통", "LG U+", "LGU", "유플러스", "통신요", "인터넷", "헬로비전"],
    "구독": ["넷플릭스", "유튜브", "스포티파이", "왓챠", "어도비", "디즈니",
             "티빙", "웨이브", "Apple.com", "NETFLIX", "YOUTUBE", "Microsoft"],
    "주거/관리": ["관리비", "전기", "수도", "가스", "월세", "임대료", "한국전력",
                  "도시가스", "아파트관리"],
    "주거/대출": ["주택금융공사", "주택담보", "전세대출", "보금자리"],
    "교육/자녀": ["학원", "교원구몬", "구몬", "수업", "교재", "수학영어", "매쓰앤리딩",
                  "어린이집", "유치원", "방과후", "키즈", "ABC", "english"],
    "운동/취미": ["헬스", "피트니스", "요가", "필라테스", "클라이밍", "수영", "골프",
                  "스포츠클럽", "그린힐", "헬스장", "체육관"],
    "보험/금융": ["보험", "삼성화재", "DB손해", "KB손해", "메리츠", "한화손해",
                  "대출이자", "이자상환", "원리금", "할부수수료"],
    "자기이체": ["IBK3615"],  # IBK 본인계좌 식별번호 — 카뱅↔IBK 이체 시 내용에 박힘
    "이체/송금": ["일반이체", "계좌간자동이체"],
    "현금/ATM": ["ATM출금", "ATM", "농협ATM", "신협ATM", "우리ATM", "현금", "수표"],
    "개인송금": ["윤태수", "정미영", "최미사", "한용순", "윤순남", "정황섭", "신인식",
                "최장훈", "조경선", "김상윤", "윤준영", "윤재선", "김유식", "안현종",
                "엄마", "아빠", "아버지", "어머니", "남편", "아내", "와이프", "형",
                "누나", "동생", "언니", "오빠"],
    "근로소득": ["급여", "월급", "상여", "보너스", "라이징테크"],
    "기타수입": ["이자", "환급", "배당"],
}


def guess_category(
    merchant: str, tx_type: str, origin: str = "", source: str = "",
    overrides: dict | None = None,
) -> str:
    """가맹점/내역 + 유형 + 원문(상대은행/거래구분) + 출처(은행)로 카테고리 추론.

    overrides: {내역 → 카테고리} 학습 매핑. 시트에서 사용자가 직접 수정한
    분류를 우선 적용. learn_category_overrides()로 생성.

    임영재 분기는 출처별로 신호가 달라 분기 처리:
      - IBK 출처:  원문에 "농협" → 어머니차입금 / "카카오뱅크·토스뱅크" → 자기이체
      - 카뱅 출처: 거래구분 "계좌간자동이체" → 자기이체 / 그 외 → 어머니(보수)
      - 그 외:     기본 어머니차입금

    우선순위:
    0) overrides 매핑 (사용자 수동 수정)
    1) 임영재 분기 (출처+원문)
    2) 양방향 규칙(부채청산)
    3) 입금: 환불·캐시백 / 키워드 / fallback "수입"
    4) 출금: 키워드 / fallback "기타"
    """
    text = (merchant or "")
    text_lower = text.lower()
    origin_lower = (origin or "").lower()
    source_norm = (source or "").lower()

    # 0) 사용자 수동 매핑 (학습) 우선
    if overrides and text in overrides:
        return overrides[text]

    # 1) 임영재 거래
    if "임영재" in text:
        if "ibk" in source_norm or "기업은행" in source_norm:
            if "농협" in origin_lower:
                return "어머니차입금"
            if any(b in origin_lower for b in ["카카오뱅크", "카뱅", "토스뱅크"]):
                return "자기이체"
            return "어머니차입금"  # 상대은행 미상 시 보수적으로 어머니
        if "카카오" in source_norm or "카뱅" in source_norm:
            # 카뱅 origin은 "카카오뱅크 | {거래구분} | {메모}" 형태
            if "계좌간자동이체" in origin_lower:
                return "자기이체"
            return "어머니차입금"
        # 기타 출처(BC카드 등)에서 임영재가 잡힐 일은 거의 없음
        return "어머니차입금"

    # 2) 양방향 (부채청산)
    for cat, kws in BIDIRECTIONAL_RULES:
        if cat == "어머니차입금":
            continue  # 위에서 처리
        for kw in kws:
            if kw.lower() in text_lower:
                return cat

    # 3) 입금
    if tx_type == "입금":
        if any(k in text for k in ["환불", "취소", "캐시백"]):
            return "환불/캐시백"
        for cat in ("근로소득", "기타수입", "개인송금"):
            for kw in CATEGORY_KEYWORDS.get(cat, []):
                if kw.lower() in text_lower:
                    return cat
        return "수입"

    # 4) 출금
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in ("근로소득", "기타수입"):
            continue
        for kw in keywords:
            if kw.lower() in text_lower:
                return category
    return "기타"

# ── 페이지 설정 ──────────────────────────────────────
st.set_page_config(
    page_title="가계부 대시보드",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS 커스텀 ────────────────────────────────────────
st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem 1.5rem;
        border-radius: 12px;
        color: white;
        text-align: center;
        margin: 0.3rem 0;
    }
    .metric-income {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
    }
    .metric-expense {
        background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
    }
    .metric-balance {
        background: linear-gradient(135deg, #4776E6 0%, #8E54E9 100%);
    }
    .metric-label { font-size: 0.85rem; opacity: 0.9; }
    .metric-value { font-size: 1.8rem; font-weight: 700; }
    [data-testid="stMetricValue"] { font-size: 1.5rem; }
</style>
""", unsafe_allow_html=True)


# ── Google Sheets 클라이언트 ──────────────────────────
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


SHEET_HEADER = ["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문", "잔액"]


def get_worksheet():
    creds_dict = dict(st.secrets["gcp_service_account"])
    sheet_id = st.secrets["GOOGLE_SHEET_ID"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(sheet_id)
    try:
        ws = sheet.worksheet("거래내역")
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet("거래내역", rows=10000, cols=12)
        ws.append_row(SHEET_HEADER)
        return ws
    # 기존 시트에 잔액 컬럼 자동 추가 (세션 1회만 체크)
    if not st.session_state.get("_balance_col_checked"):
        header = ws.row_values(1)
        if header and "잔액" not in header:
            ws.update_cell(1, len(header) + 1, "잔액")
        st.session_state["_balance_col_checked"] = True
    return ws


# ── Google Sheets 로드 ────────────────────────────────
@st.cache_data(ttl=300)  # 5분 캐시
def load_data():
    try:
        ws = get_worksheet()
        data = ws.get_all_records()
        df = pd.DataFrame(data)

        if df.empty:
            return pd.DataFrame(columns=SHEET_HEADER)

        df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
        df["금액"] = pd.to_numeric(df["금액"], errors="coerce").fillna(0)
        if "잔액" in df.columns:
            df["잔액"] = pd.to_numeric(df["잔액"], errors="coerce")
        df = df.dropna(subset=["날짜"])
        df = df.sort_values("날짜", ascending=False)
        return df

    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        return pd.DataFrame(columns=SHEET_HEADER)


# ── 현대카드 Excel/CSV/HTML 파서 ──────────────────────
HYUNDAI_COL_ALIASES = {
    "날짜": ["이용일", "이용일자", "거래일", "거래일자", "사용일", "승인일자", "승인일"],
    "시간": ["이용시간", "거래시간", "승인시간", "승인시각", "사용시간"],
    "내역": ["이용가맹점", "가맹점명", "가맹점", "이용처", "사용처"],
    "이용금액": ["이용금액", "승인금액", "이용금액(원)"],
    "결제원금": ["결제원금"],  # 명세서: 할부의 이번달 분담액 (BC카드 원금(KRW)과 동일)
    "구분": ["이용구분", "거래구분", "구분", "할부", "할부개월", "할부/회차"],
    "카드": ["이용카드", "카드구분", "카드종류"],
}


def _find_header_row(df_raw: pd.DataFrame, max_scan: int = 15) -> int:
    """현대카드 엑셀은 상단에 메타 행이 있을 수 있어, 헤더 행을 휴리스틱으로 탐색"""
    targets = set(sum(HYUNDAI_COL_ALIASES.values(), []))
    for idx in range(min(max_scan, len(df_raw))):
        row_vals = [str(v).strip() for v in df_raw.iloc[idx].tolist()]
        if sum(1 for v in row_vals if v in targets) >= 2:
            return idx
    return 0


def _match_column(columns, aliases):
    cols = [str(c).strip() for c in columns]
    for alias in aliases:
        for i, c in enumerate(cols):
            if c == alias:
                return columns[i]
    # 부분 일치 fallback
    for alias in aliases:
        for i, c in enumerate(cols):
            if alias in c:
                return columns[i]
    return None


def _read_hyundai_html_as_df(raw_bytes: bytes) -> pd.DataFrame:
    """HTML 위장 .xls를 DataFrame으로 (헤더 없이 모든 행 포함)"""
    from bs4 import BeautifulSoup
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw_bytes.decode("utf-8", errors="replace")

    soup = BeautifulSoup(text, "lxml")
    tables = soup.find_all("table")
    # 헤더(별칭 다수 포함)가 있는 표를 우선 선택
    targets = set(sum(HYUNDAI_COL_ALIASES.values(), []))
    chosen = None
    for tbl in tables:
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if sum(1 for c in cells if c in targets) >= 2:
                chosen = tbl
                break
        if chosen:
            break
    if chosen is None and tables:
        chosen = tables[0]
    if chosen is None:
        raise ValueError("HTML에서 표를 찾을 수 없습니다")

    rows_data = []
    max_cols = 0
    for tr in chosen.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        rows_data.append(cells)
        max_cols = max(max_cols, len(cells))
    # 길이 정렬
    rows_data = [r + [""] * (max_cols - len(r)) for r in rows_data]
    return pd.DataFrame(rows_data)


def _normalize_korean_date(s: str) -> str:
    """`2026년 05월 30일` / `2026.05.30` / `2026-05-30` 등을 ISO로 변환"""
    if not s:
        return ""
    s = str(s).strip()
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def parse_hyundai_file(uploaded_file) -> pd.DataFrame:
    """현대카드 Excel/CSV/HTML → 표준 거래 DataFrame.
    현대카드 웹의 .xls 다운로드는 실제로는 HTML 표 형식이므로 자동 감지."""
    name = (uploaded_file.name or "").lower()
    raw = uploaded_file.read()
    if not isinstance(raw, bytes):
        raw = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    head = raw[:2048].decode("utf-8", errors="ignore").lower()
    is_html = ("<html" in head or "<!doctype" in head or "<table" in head
               or "<script" in head or "<!--" in head)

    if is_html:
        raw_df = _read_hyundai_html_as_df(raw)
    elif name.endswith(".csv"):
        for enc in ("cp949", "utf-8"):
            try:
                raw_df = pd.read_csv(io.BytesIO(raw), encoding=enc, header=None)
                break
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        else:
            raise ValueError("CSV 디코딩 실패")
    else:
        raw_df = pd.read_excel(io.BytesIO(raw), header=None)

    header_idx = _find_header_row(raw_df)
    df = raw_df.iloc[header_idx + 1:].copy()
    df.columns = raw_df.iloc[header_idx].tolist()
    df = df.dropna(how="all").reset_index(drop=True)

    col_date = _match_column(df.columns, HYUNDAI_COL_ALIASES["날짜"])
    col_amount = _match_column(df.columns, HYUNDAI_COL_ALIASES["이용금액"])
    col_payment = _match_column(df.columns, HYUNDAI_COL_ALIASES["결제원금"])
    col_merchant = _match_column(df.columns, HYUNDAI_COL_ALIASES["내역"])
    col_time = _match_column(df.columns, HYUNDAI_COL_ALIASES["시간"])
    col_type = _match_column(df.columns, HYUNDAI_COL_ALIASES["구분"])
    col_card = _match_column(df.columns, HYUNDAI_COL_ALIASES["카드"])

    if not col_date or not (col_amount or col_payment):
        raise ValueError(
            f"필수 컬럼을 찾을 수 없어요. 감지된 컬럼: {list(df.columns)}"
        )

    out = pd.DataFrame()
    out["날짜"] = df[col_date].astype(str).apply(_normalize_korean_date)
    out["시간"] = (
        df[col_time].astype(str).str.strip() if col_time else ""
    )
    out["출처"] = "현대카드"

    def _to_amount_signed(v):
        """부호 보존 정수 변환. 음수 거래(취소/환불)를 식별하기 위함."""
        if pd.isna(v):
            return 0
        s = re.sub(r"[^\d\-]", "", str(v))
        try:
            return int(s) if s and s != "-" else 0
        except ValueError:
            return 0

    # 결제원금 컬럼이 있으면 (월간 명세서 형식) 그것만 사용:
    # - 양수 결제원금 = 이번달 청구 분담액 → 출금
    # - 음수 결제원금 = 매출할인/환불 (해당 달에서 차감) → 입금
    # - 0 = 다음 달로 이월된 거래 (이용금액 음수의 환불도 차기 달 음수 결제원금으로 나타남)
    # 결제원금 컬럼이 없으면 (실시간 이용내역 형식) 이용금액을 사용
    def _resolve_amount(row):
        if col_payment is not None:
            return _to_amount_signed(row.get(col_payment))
        if col_amount is not None:
            return _to_amount_signed(row.get(col_amount))
        return 0

    raw_amt = df.apply(_resolve_amount, axis=1)
    out["금액"] = raw_amt.abs()

    # 유형: 음수 거래(매출할인/취소/환불)는 자동 입금, 그 외에는 구분 컬럼 기반
    def _to_type(idx):
        v = raw_amt.iloc[idx]
        if v < 0:
            return "입금"
        if col_type is not None:
            s = str(df[col_type].iloc[idx])
            if any(k in s for k in ["취소", "환불", "입금"]):
                return "입금"
        return "출금"

    out["유형"] = [_to_type(i) for i in range(len(df))]

    out["내역"] = (
        df[col_merchant].astype(str).str.strip() if col_merchant else "현대카드 사용"
    )
    out["카테고리"] = [
        guess_category(m, t) for m, t in zip(out["내역"], out["유형"])
    ]
    # 원문에 이용카드(본인/가족 X3 등) 보존 — 시트에서 카드별 필터 가능
    if col_card:
        out["원문"] = "현대카드 | " + df[col_card].astype(str).str.strip()
    else:
        out["원문"] = "현대카드 업로드"

    # 합계/소계 행 제거 (현대카드 HTML 마지막에 "국내 일시불 소계 N건",
    # "본인 소계", "X 할인 소계" 등 보조 합계가 다수)
    SKIP_KEYWORDS = ("소계", "합계", "총계")
    out["내역"] = out["내역"].astype(str).str.strip()
    mask_skip = out["내역"].apply(
        lambda m: (not m) or any(k in m for k in SKIP_KEYWORDS)
    )
    out = out[~mask_skip]
    # 날짜가 정상 형식(YYYY-MM-DD)이 아닌 행 제거
    out = out[out["날짜"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    out = out[out["금액"] > 0].reset_index(drop=True)
    return out


# ── IBK 기업은행 입출금 HTML(.xls) 파서 ─────────────
def parse_ibk_account_file(uploaded_file) -> pd.DataFrame:
    """IBK기업은행 거래내역조회 HTML(.xls) → 표준 거래 DataFrame.
    실제 파일은 HTML 형태의 표를 .xls 확장자로 내려받는 형식이라
    BeautifulSoup으로 직접 파싱한다."""
    from bs4 import BeautifulSoup

    raw = uploaded_file.read()
    if isinstance(raw, bytes):
        for enc in ("utf-8", "cp949", "euc-kr"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
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
        raise ValueError("거래내역 표를 찾을 수 없습니다. 파일 형식 확인 필요.")

    rows = tx_table.find_all("tr")
    header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]

    def col_idx(name):
        try:
            return header.index(name)
        except ValueError:
            return None

    i_dt = col_idx("거래일시")
    i_out = col_idx("출금")
    i_in = col_idx("입금")
    i_balance = col_idx("거래후잔액") or col_idx("잔액") or col_idx("거래 후 잔액")
    i_content = col_idx("거래내용")
    i_msg = col_idx("송금메시지")
    i_bank = col_idx("상대은행")
    i_type = col_idx("거래구분")
    i_holder = col_idx("상대계좌예금주명")

    if i_dt is None or i_out is None or i_in is None:
        raise ValueError(f"필수 컬럼 누락. 감지된 컬럼: {header}")

    def _to_int(s):
        s = re.sub(r"[^\d\-]", "", str(s or ""))
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
        out_amt = _to_int(cells[i_out])
        in_amt = _to_int(cells[i_in])
        if out_amt > 0:
            tx_type, amount = "출금", out_amt
        elif in_amt > 0:
            tx_type, amount = "입금", in_amt
        else:
            continue

        content = cells[i_content] if i_content is not None else ""
        holder = cells[i_holder] if i_holder is not None else ""
        kind = cells[i_type] if i_type is not None else ""
        bank = cells[i_bank] if i_bank is not None else ""
        msg = cells[i_msg] if i_msg is not None else ""
        balance = _to_int(cells[i_balance]) if (i_balance is not None and len(cells) > i_balance) else None

        merchant = content or holder or msg or "알 수 없음"
        origin_parts = [p for p in [kind, bank, holder] if p]
        origin = "IBK통장|" + " / ".join(origin_parts)

        txs.append({
            "날짜": dt.strftime("%Y-%m-%d"),
            "시간": dt.strftime("%H:%M"),
            "출처": "IBK기업은행",
            "유형": tx_type,
            "금액": amount,
            "내역": merchant[:50],
            "카테고리": guess_category(merchant, tx_type, origin, "IBK기업은행"),
            "원문": origin[:100],
            "잔액": balance,
        })
    return pd.DataFrame(txs)


def parse_kakaobank_file(uploaded_file, password: str | None = None) -> pd.DataFrame:
    """카카오뱅크 거래내역 .xlsx → 표준 거래 DataFrame.
    카카오뱅크 앱에서 받은 파일은 Microsoft Agile Encryption으로 잠겨있을 수 있어
    비밀번호(보통 생년월일 6자리)가 필요하다.
    """
    raw = uploaded_file.read()
    buf = io.BytesIO(raw)

    # 암호화 여부 확인 후 복호화
    try:
        import msoffcrypto
        buf.seek(0)
        of = msoffcrypto.OfficeFile(buf)
        if of.is_encrypted():
            if not password:
                raise ValueError(
                    "이 파일은 비밀번호로 잠겨있어요. 카카오뱅크에서 다운받을 때 설정한 비밀번호(보통 생년월일 6자리)를 입력해주세요."
                )
            decrypted = io.BytesIO()
            of.load_key(password=password)
            of.decrypt(decrypted)
            decrypted.seek(0)
            buf = decrypted
        else:
            buf.seek(0)
    except ImportError:
        buf.seek(0)
    except ValueError:
        raise
    except Exception:
        buf.seek(0)

    df_raw = pd.read_excel(buf, sheet_name=0, header=None)

    # 헤더 행 자동 감지 (거래일시 + 거래금액)
    header_idx = None
    for i in range(min(20, len(df_raw))):
        row = [str(v).strip() for v in df_raw.iloc[i]]
        if "거래일시" in row and "거래금액" in row:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            f"카카오뱅크 거래내역 헤더 행을 찾을 수 없어요. 감지된 첫 행: {list(df_raw.iloc[0])}"
        )

    df = df_raw.iloc[header_idx + 1:].copy()
    df.columns = [str(v).strip() for v in df_raw.iloc[header_idx]]

    def col(*aliases):
        for a in aliases:
            if a in df.columns:
                return a
        return None

    c_dt = col("거래일시")
    c_kind = col("구분")
    c_amt = col("거래금액")
    c_balance = col("거래 후 잔액", "거래후잔액", "잔액")
    c_txkind = col("거래구분")
    c_content = col("내용")
    c_memo = col("메모")
    if not c_dt or not c_amt:
        raise ValueError(f"필수 컬럼 누락. 감지된 컬럼: {list(df.columns)}")

    def _to_int(s):
        s = re.sub(r"[^\d\-]", "", str(s) if not pd.isna(s) else "")
        try:
            return int(s) if s and s != "-" else 0
        except ValueError:
            return 0

    txs = []
    for _, row in df.iterrows():
        dt_raw = row[c_dt]
        if pd.isna(dt_raw):
            continue
        try:
            dt = pd.to_datetime(dt_raw)
        except (ValueError, TypeError):
            continue

        amt_signed = _to_int(row[c_amt])
        if amt_signed == 0:
            continue

        # "구분" 컬럼이 명시되어 있으면 우선 사용, 없으면 부호로 판단
        kind = str(row[c_kind]).strip() if c_kind and not pd.isna(row[c_kind]) else ""
        if kind == "출금":
            tx_type = "출금"
        elif kind == "입금":
            tx_type = "입금"
        else:
            tx_type = "출금" if amt_signed < 0 else "입금"
        amount = abs(amt_signed)

        def _cell(name):
            if not name or name not in df.columns:
                return ""
            v = row[name]
            return "" if pd.isna(v) else str(v).strip()

        content = _cell(c_content)
        txkind = _cell(c_txkind)
        memo = _cell(c_memo)
        balance = _to_int(row[c_balance]) if c_balance and not pd.isna(row[c_balance]) else None

        merchant = content or txkind or "카카오뱅크 거래"
        origin = " | ".join(p for p in ["카카오뱅크", txkind, memo] if p)

        txs.append({
            "날짜": dt.strftime("%Y-%m-%d"),
            "시간": dt.strftime("%H:%M"),
            "출처": "카카오뱅크",
            "유형": tx_type,
            "금액": amount,
            "내역": merchant[:50],
            "카테고리": guess_category(merchant, tx_type, origin, "카카오뱅크"),
            "원문": origin[:100],
            "잔액": balance,
        })
    return pd.DataFrame(txs)


def append_transactions_to_sheet(transactions: list[dict]) -> int:
    """중복 제외하고 시트에 추가, 추가 건수 반환"""
    ws = get_worksheet()
    existing = ws.get_all_values()
    existing_keys = set()
    for row in existing[1:]:
        if len(row) >= 6:
            # 날짜_출처_금액_내역
            existing_keys.add(f"{row[0]}_{row[2]}_{row[4]}_{row[5]}")

    new_rows = []
    for tx in transactions:
        key = f"{tx['날짜']}_{tx['출처']}_{tx['금액']}_{tx['내역']}"
        if key in existing_keys:
            continue
        existing_keys.add(key)
        bal = tx.get("잔액")
        new_rows.append([
            tx["날짜"], tx.get("시간", ""), tx["출처"], tx["유형"],
            tx["금액"], tx["내역"], tx["카테고리"], tx.get("원문", ""),
            "" if bal is None else bal,
        ])

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    return len(new_rows)


def learn_category_overrides(
    rows: list[list[str]], header: list[str],
    min_count: int = 2, confidence: float = 0.8,
) -> dict[str, str]:
    """시트 행에서 (내역 → 카테고리) 매핑 학습.

    같은 내역이 min_count회 이상 나오고, 그중 한 카테고리가 confidence
    비율 이상이면서 그 카테고리가 키워드 자동 분류 결과와 다르면
    사용자가 직접 수정한 매핑으로 간주.

    Returns: {내역 → 카테고리}
    """
    try:
        cat_i = header.index("카테고리")
        merch_i = header.index("내역")
        type_i = header.index("유형")
    except ValueError:
        return {}
    origin_i = header.index("원문") if "원문" in header else None
    source_i = header.index("출처") if "출처" in header else None

    from collections import Counter, defaultdict
    by_merch: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        if len(row) <= max(cat_i, merch_i, type_i):
            continue
        merch = row[merch_i].strip()
        if not merch:
            continue
        cat = row[cat_i].strip()
        ty = row[type_i].strip()
        org = row[origin_i] if origin_i is not None and len(row) > origin_i else ""
        src = row[source_i] if source_i is not None and len(row) > source_i else ""
        by_merch[merch].append((cat, ty, org, src))

    overrides: dict[str, str] = {}
    for merch, observations in by_merch.items():
        if len(observations) < min_count:
            continue
        cats = Counter(o[0] for o in observations if o[0])
        if not cats:
            continue
        top_cat, top_n = cats.most_common(1)[0]
        if top_n / len(observations) < confidence:
            continue
        # 키워드 자동 분류와 비교 — 차이날 때만 override로 등록
        # (자동 분류와 같으면 굳이 학습 안 해도 같은 결과)
        sample_ty, sample_org, sample_src = observations[0][1:]
        auto = guess_category(merch, sample_ty, sample_org, sample_src)
        if auto != top_cat:
            overrides[merch] = top_cat
    return overrides


def recategorize_all_rows() -> tuple[int, int, int]:
    """시트 모든 행에 guess_category를 다시 적용.
    Returns: (변경된 건수, 전체 건수, 학습된 override 건수)
    """
    ws = get_worksheet()
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return 0, 0, 0
    header = rows[0]
    try:
        cat_idx = header.index("카테고리")
        type_idx = header.index("유형")
        merch_idx = header.index("내역")
    except ValueError:
        return 0, len(rows) - 1, 0
    # 원문/출처 컬럼은 선택 — 있으면 임영재 분기 정확도 향상
    try:
        origin_idx = header.index("원문")
    except ValueError:
        origin_idx = None
    try:
        source_idx = header.index("출처")
    except ValueError:
        source_idx = None

    # 사용자 수동 매핑 학습 — 시트의 현재 (내역 → 카테고리) 분포에서
    overrides = learn_category_overrides(rows[1:], header)

    cat_col_letter = chr(ord("A") + cat_idx)
    updates = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= max(cat_idx, type_idx, merch_idx):
            continue
        old_cat = row[cat_idx]
        origin = row[origin_idx] if origin_idx is not None and len(row) > origin_idx else ""
        source = row[source_idx] if source_idx is not None and len(row) > source_idx else ""
        new_cat = guess_category(row[merch_idx], row[type_idx], origin, source, overrides)
        if new_cat != old_cat:
            updates.append({
                "range": f"{cat_col_letter}{i}",
                "values": [[new_cat]],
            })

    if updates:
        # 큰 시트는 일부 환경에서 한 번에 보내면 제한에 걸릴 수 있어 500건씩 배치
        for batch_start in range(0, len(updates), 500):
            ws.batch_update(
                updates[batch_start:batch_start + 500],
                value_input_option="USER_ENTERED",
            )
    return len(updates), len(rows) - 1, len(overrides)


def pair_self_transfers_in_sheet(tolerance_days: int = 2) -> tuple[int, int]:
    """IBK ↔ 카뱅 임영재 거래를 날짜+금액으로 매칭해 자기이체로 재분류.

    카뱅 데이터엔 상대은행 정보가 없어 일부 본인이체가 어머니차입금으로 잘못 분류됨.
    IBK 데이터의 임영재 거래(원문에 상대은행 박힘)와 양방향 매칭해 짝이 맞는
    카뱅 행을 자기이체로 이동.

    매칭 규칙:
      - 출처 IBK 임영재 ↔ 출처 카뱅 임영재
      - 동일 금액
      - 반대 유형 (IBK 출금 ↔ 카뱅 입금 / IBK 입금 ↔ 카뱅 출금)
      - 날짜 차이 ≤ tolerance_days
      - 가까운 날짜 우선 (그리디)

    Returns: (페어링된 카뱅 행 수, 검사한 카뱅 후보 수)
    """
    ws = get_worksheet()
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return 0, 0
    header = rows[0]
    try:
        date_idx = header.index("날짜")
        source_idx = header.index("출처")
        type_idx = header.index("유형")
        amount_idx = header.index("금액")
        merch_idx = header.index("내역")
        cat_idx = header.index("카테고리")
    except ValueError:
        return 0, 0

    def parse_amt(s):
        try:
            return int(re.sub(r"[^\d\-]", "", str(s)))
        except (ValueError, TypeError):
            return 0

    ibk_imy = []  # (row#, date, type, amount)
    kk_candidates = []  # 매칭 대상: 카뱅 임영재 + 어머니차입금
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= max(date_idx, source_idx, type_idx, amount_idx, merch_idx, cat_idx):
            continue
        if row[merch_idx].strip() != "임영재":
            continue
        try:
            d = datetime.strptime(row[date_idx][:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        amt = parse_amt(row[amount_idx])
        src = row[source_idx]
        ty = row[type_idx]
        if src == "IBK기업은행":
            ibk_imy.append((i, d, ty, amt))
        elif src == "카카오뱅크" and row[cat_idx] == "어머니차입금":
            kk_candidates.append((i, d, ty, amt))

    used_ibk = set()
    matched_kk_rows = []
    for kk_i, kk_d, kk_t, kk_amt in kk_candidates:
        target_t = "입금" if kk_t == "출금" else "출금"
        best = None
        for ibk_i, ibk_d, ibk_t, ibk_amt in ibk_imy:
            if ibk_i in used_ibk:
                continue
            if ibk_amt != kk_amt or ibk_t != target_t:
                continue
            days = abs((kk_d - ibk_d).days)
            if days > tolerance_days:
                continue
            if best is None or days < best[0]:
                best = (days, ibk_i)
        if best is not None:
            used_ibk.add(best[1])
            matched_kk_rows.append(kk_i)

    if not matched_kk_rows:
        return 0, len(kk_candidates)

    cat_col_letter = chr(ord("A") + cat_idx)
    updates = [
        {"range": f"{cat_col_letter}{i}", "values": [["자기이체"]]}
        for i in matched_kk_rows
    ]
    for batch_start in range(0, len(updates), 500):
        ws.batch_update(
            updates[batch_start:batch_start + 500],
            value_input_option="USER_ENTERED",
        )
    return len(matched_kk_rows), len(kk_candidates)


DEFAULT_BUDGET = {
    "식비": 600000, "교통": 100000, "쇼핑": 300000,
    "주거/관리": 300000, "주거/대출": 800000,
    "통신": 100000, "구독": 30000,
    "보험/금융": 200000, "교육/자녀": 500000,
    "운동/취미": 100000, "의료": 50000, "여행/항공": 0,
}


def get_budget_worksheet():
    """예산 워크시트 가져오기/생성 (없으면 디폴트 예산 채워 생성)."""
    ws_main = get_worksheet()
    sheet = ws_main.spreadsheet
    try:
        return sheet.worksheet("예산")
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet("예산", rows=30, cols=3)
        ws.append_row(["카테고리", "월 예산"])
        ws.append_rows([[c, v] for c, v in DEFAULT_BUDGET.items()])
        return ws


@st.cache_data(ttl=300)
def load_budget() -> dict:
    """예산 워크시트 로드. 시트가 없으면 자동 생성. {카테고리: 월예산}."""
    try:
        ws = get_budget_worksheet()
        budget = {}
        for r in ws.get_all_records():
            cat = str(r.get("카테고리", "")).strip()
            amt_raw = r.get("월 예산", 0)
            try:
                amt = int(amt_raw) if amt_raw else 0
            except (TypeError, ValueError):
                continue
            if cat and amt > 0:
                budget[cat] = amt
        return budget
    except Exception:
        return {}


def detect_outliers(
    df_src, year: int, month: int, threshold_ratio: float = 3.0,
    min_history: int = 5,
) -> pd.DataFrame:
    """주어진 (연,월) 거래 중 같은 카테고리 중앙값 대비 N배 초과 거래 추출.

    중앙값(median)은 평균보다 outlier에 robust. 카테고리별 시트 전체
    이력에서 중앙값을 구하고, 이번 달 거래가 그 배수의 threshold_ratio
    이상이면 이상치로 표시.

    Returns: DataFrame [날짜, 내역, 카테고리, 금액, 중앙값, 배수]
    """
    if df_src is None or df_src.empty:
        return pd.DataFrame()
    exp = df_src[
        (df_src["유형"] == "출금") & ~df_src["카테고리"].isin(NON_PNL_CATEGORIES)
    ]
    if exp.empty:
        return pd.DataFrame()
    medians = exp.groupby("카테고리")["금액"].agg(["median", "count"])
    target = exp[
        (exp["날짜"].dt.year == year) & (exp["날짜"].dt.month == month)
    ]
    rows = []
    for _, r in target.iterrows():
        info = medians.loc[r["카테고리"]] if r["카테고리"] in medians.index else None
        if info is None or int(info["count"]) < min_history:
            continue
        median_val = float(info["median"])
        if median_val <= 0:
            continue
        ratio = r["금액"] / median_val
        if ratio >= threshold_ratio:
            rows.append({
                "날짜": r["날짜"].strftime("%Y-%m-%d"),
                "내역": r["내역"],
                "카테고리": r["카테고리"],
                "금액": int(r["금액"]),
                "카테고리 중앙값": int(median_val),
                "배수": round(ratio, 1),
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("배수", ascending=False).reset_index(drop=True)


def match_card_charges_to_usage(df_src, window_days: int = 35) -> pd.DataFrame:
    """카드대금 자동이체(부채청산)와 그 직전 window_days 사용내역 매칭.

    각 청구 행에 대해 같은 카드사 사용 행 합계를 계산해 청구액 vs 사용액
    차이를 보여줌. 차이가 크면 장기할부·환불·이월 등 정합성 점검 필요.
    """
    if df_src is None or df_src.empty:
        return pd.DataFrame()
    charges = df_src[
        (df_src["카테고리"] == "부채청산") & (df_src["유형"] == "출금")
    ].copy()
    if charges.empty:
        return pd.DataFrame()

    def card_of(text: str) -> str | None:
        t = (text or "").lower()
        if "현대" in t:
            return "현대카드"
        if "비씨" in t or "bc" in t:
            return "BC카드"
        if "kb" in t:
            return "KB카드"
        if "삼성" in t:
            return "삼성카드"
        if "신한" in t:
            return "신한카드"
        if "롯데" in t:
            return "롯데카드"
        return None

    charges["카드사"] = charges["내역"].apply(card_of)
    charges = charges.dropna(subset=["카드사"])

    use_df = df_src[
        df_src["출처"].isin(CARD_SOURCES)
        & (df_src["유형"] == "출금")
        & (~df_src["카테고리"].isin(NON_PNL_CATEGORIES))
    ].copy()

    rows = []
    for _, ch in charges.iterrows():
        card = ch["카드사"]
        charge_date = ch["날짜"]
        same_card = use_df[use_df["출처"] == card]
        win_start = charge_date - pd.Timedelta(days=window_days)
        prior_use = same_card[
            (same_card["날짜"] >= win_start) & (same_card["날짜"] < charge_date)
        ]
        usage = int(prior_use["금액"].sum())
        charged = int(ch["금액"])
        rows.append({
            "청구일": charge_date.strftime("%Y-%m-%d"),
            "카드사": card,
            "청구액": charged,
            f"사용액({window_days}일 직전)": usage,
            "차이": charged - usage,
            "사용 건수": len(prior_use),
        })
    return pd.DataFrame(rows).sort_values("청구일", ascending=False).reset_index(drop=True)


def find_invalid_rows() -> list[dict]:
    """시트 행 중 형식이 이상한 행 식별.

    검사 항목:
      - 날짜가 YYYY-MM-DD 형식 아닌 행
      - 유형이 '출금'/'입금'이 아닌 행
      - 금액이 양의 정수로 파싱 안 되는 행
      - 출처/내역이 비어있는 행

    Returns: [{행번호, 날짜, 출처, 유형, 금액, 내역, 문제: [...]}]
    """
    ws = get_worksheet()
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return []
    header = rows[0]
    try:
        date_i = header.index("날짜")
        src_i = header.index("출처")
        type_i = header.index("유형")
        amt_i = header.index("금액")
        merch_i = header.index("내역")
    except ValueError:
        return []

    invalid = []
    for i, r in enumerate(rows[1:], start=2):
        problems = []
        if len(r) <= max(date_i, src_i, type_i, amt_i, merch_i):
            problems.append("컬럼 부족")
        else:
            # 날짜
            try:
                datetime.strptime(r[date_i][:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                problems.append(f"날짜 형식 오류: '{r[date_i]}'")
            # 유형
            if r[type_i].strip() not in ("출금", "입금"):
                problems.append(f"유형 비정상: '{r[type_i]}'")
            # 금액
            try:
                amt_clean = re.sub(r"[^\d\-]", "", r[amt_i])
                amt = int(amt_clean) if amt_clean and amt_clean != "-" else 0
                if amt <= 0:
                    problems.append(f"금액 0 이하: '{r[amt_i]}'")
            except (ValueError, TypeError):
                problems.append(f"금액 파싱 실패: '{r[amt_i]}'")
            # 출처/내역
            if not r[src_i].strip():
                problems.append("출처 누락")
            if not r[merch_i].strip():
                problems.append("내역 누락")
        if problems:
            invalid.append({
                "행": i,
                "날짜": r[date_i] if len(r) > date_i else "",
                "출처": r[src_i] if len(r) > src_i else "",
                "유형": r[type_i] if len(r) > type_i else "",
                "금액": r[amt_i] if len(r) > amt_i else "",
                "내역": r[merch_i] if len(r) > merch_i else "",
                "문제": " · ".join(problems),
            })
    return invalid


def apply_manual_category(merchant: str, category: str) -> int:
    """주어진 내역의 모든 시트 행 카테고리를 일괄 변경. 변경 건수 반환.

    매칭은 내역 정확 일치(strip 후). 학습 시스템이 같은 매핑을 자동
    인식해 다음 업로드부터 적용됨.
    """
    ws = get_worksheet()
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return 0
    header = rows[0]
    try:
        cat_i = header.index("카테고리")
        merch_i = header.index("내역")
    except ValueError:
        return 0
    cat_col = chr(ord("A") + cat_i)
    target = (merchant or "").strip()
    if not target:
        return 0
    updates = []
    for i, r in enumerate(rows[1:], start=2):
        if len(r) <= max(cat_i, merch_i):
            continue
        if r[merch_i].strip() == target and r[cat_i] != category:
            updates.append({"range": f"{cat_col}{i}", "values": [[category]]})
    if updates:
        for batch_start in range(0, len(updates), 500):
            ws.batch_update(
                updates[batch_start:batch_start + 500],
                value_input_option="USER_ENTERED",
            )
    return len(updates)


def _maybe_autosave(parsed_df, source_label: str, uploaded_file) -> None:
    """auto_save 토글이 켜져 있고 같은 파일을 아직 자동 저장한 적 없으면 즉시 시트에 append."""
    if not st.session_state.get("auto_save_enabled", True):
        return
    file_key = f"_autosaved::{source_label}::{uploaded_file.name}::{uploaded_file.size}"
    if st.session_state.get(file_key):
        return
    try:
        added = append_transactions_to_sheet(parsed_df.to_dict(orient="records"))
    except Exception as e:
        st.error(f"⚡ 자동 저장 오류: {e}")
        return
    st.session_state[file_key] = True
    if added:
        st.success(
            f"⚡ 자동 저장: {added}건 추가 (중복 {len(parsed_df) - added}건 제외) — "
            "편집 후 아래 버튼으로 재저장도 가능"
        )
        st.cache_data.clear()
    else:
        st.info(f"⚡ 자동 저장: 모두 중복 ({len(parsed_df)}건) — 시트 변화 없음")


# ── 사이드바 ──────────────────────────────────────────
st.sidebar.title("💰 가계부")
st.sidebar.markdown("---")

df_all = load_data()

if not df_all.empty:
    min_date = df_all["날짜"].min().date()
    max_date = df_all["날짜"].max().date()
else:
    min_date = max_date = date.today()

# 월 선택
now = datetime.now()
months = []
for i in range(12):
    m = now.month - i
    y = now.year
    while m <= 0:
        m += 12
        y -= 1
    months.append(f"{y}년 {m:02d}월")

selected_month = st.sidebar.selectbox("📅 월 선택", months, index=0)
year = int(selected_month[:4])
month = int(selected_month[6:8])

# 카테고리 필터
if not df_all.empty and "카테고리" in df_all.columns:
    all_cats = ["전체"] + sorted(df_all["카테고리"].unique().tolist())
else:
    all_cats = ["전체"]
selected_cat = st.sidebar.selectbox("🏷️ 카테고리", all_cats)

# 데이터 필터링
df = df_all.copy()
if not df.empty:
    df = df[(df["날짜"].dt.year == year) & (df["날짜"].dt.month == month)]
    if selected_cat != "전체":
        df = df[df["카테고리"] == selected_cat]

# 새로고침
if st.sidebar.button("🔄 데이터 새로고침"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ 동기화 옵션")
st.sidebar.checkbox(
    "⚡ 업로드 시 자동 저장",
    value=True,
    key="auto_save_enabled",
    help="파일을 올리면 미리보기와 함께 즉시 시트에 저장합니다. 중복은 자동 제외돼요.",
)

if st.sidebar.button("🏷️ 카테고리 일괄 재분류", help="새 키워드 규칙을 시트의 모든 기존 행에 소급 적용"):
    with st.spinner("시트 전체를 재분류 중..."):
        try:
            changed, total, learned = recategorize_all_rows()
            if changed:
                msg = f"✅ {total}건 중 {changed}건 업데이트"
                if learned:
                    msg += f" · 사용자 수동 매핑 {learned}개 학습 적용"
                st.sidebar.success(msg)
                st.cache_data.clear()
            else:
                info = f"변경 없음 ({total}건 모두 최신)"
                if learned:
                    info += f" · 학습 매핑 {learned}개"
                st.sidebar.info(info)
        except Exception as e:
            st.sidebar.error(f"재분류 오류: {e}")

if st.sidebar.button(
    "🔄 자기이체 자동 페어링",
    help="IBK ↔ 카뱅 임영재 거래를 날짜+금액으로 매칭해 자기이체로 재분류 (카뱅 어머니차입금 후보 → 자기이체)"
):
    with st.spinner("IBK ↔ 카뱅 페어링 중..."):
        try:
            paired, candidates = pair_self_transfers_in_sheet()
            if paired:
                st.sidebar.success(
                    f"✅ 카뱅 어머니차입금 {candidates}건 중 {paired}건을 IBK 행과 매칭 → 자기이체로 이동"
                )
                st.cache_data.clear()
            else:
                st.sidebar.info(
                    f"매칭된 페어 없음 (검사 후보 {candidates}건)"
                )
        except Exception as e:
            st.sidebar.error(f"페어링 오류: {e}")

if st.sidebar.button(
    "🧹 시트 정합성 점검",
    help="날짜·유형·금액·출처·내역 형식이 잘못된 행을 찾아 표시 (CSV import 시 깨진 행 식별용)",
):
    with st.spinner("시트 행 검사 중..."):
        try:
            st.session_state["_invalid_rows"] = find_invalid_rows()
            n = len(st.session_state["_invalid_rows"])
            if n:
                st.sidebar.warning(f"⚠️ 형식 이상 {n}건 — 메인 화면 확인")
            else:
                st.sidebar.success("✅ 모든 행 정상")
        except Exception as e:
            st.sidebar.error(f"점검 오류: {e}")

st.sidebar.markdown("---")

# 어머니 차입금 누적 (전체 기간)
if not df_all.empty and "카테고리" in df_all.columns:
    mom = df_all[df_all["카테고리"] == "어머니차입금"]
    if not mom.empty:
        borrowed = mom[mom["유형"] == "입금"]["금액"].sum()
        repaid = mom[mom["유형"] == "출금"]["금액"].sum()
        net = borrowed - repaid
        st.sidebar.markdown("##### 🏠 어머니 차입금 누적")
        st.sidebar.caption(f"(시트 전체 기간 기준)")
        st.sidebar.metric(
            label="순 부채 변동",
            value=f"{net:+,.0f}원",
            help="+면 빌린 게 더 많음(부채 증가), −면 갚은 게 더 많음(부채 감소)",
        )
        st.sidebar.caption(
            f"빌림 {borrowed:,.0f}원 / 갚음 {repaid:,.0f}원 ({len(mom)}건)"
        )

# 카카오뱅크 마통 잔액 (가장 최근 거래)
if not df_all.empty and "잔액" in df_all.columns:
    kk_rows = df_all[(df_all["출처"] == "카카오뱅크") & df_all["잔액"].notna()]
    if not kk_rows.empty:
        latest = kk_rows.sort_values("날짜", ascending=False).iloc[0]
        latest_bal = int(latest["잔액"])
        st.sidebar.markdown("##### 💛 카카오뱅크 잔액")
        st.sidebar.metric(
            label=f"{latest['날짜'].strftime('%Y-%m-%d')} 기준",
            value=f"{latest_bal:,.0f}원",
            help="마이너스(−)면 마통 사용 중. 0보다 작아질수록 부채 ↑",
        )

st.sidebar.markdown("---")
st.sidebar.caption("현대카드 내역은 수동 업로드 필요")

# ── 메인 화면 ─────────────────────────────────────────
st.title(f"💰 {selected_month} 가계부")


def _month_pnl(df_src, y, m):
    """주어진 (연,월)의 손익 지표 반환: (수입, 지출, 손익, 근로소득, 고정비, 변동비)."""
    if df_src is None or df_src.empty:
        return 0, 0, 0, 0, 0, 0
    sub = df_src[(df_src["날짜"].dt.year == y) & (df_src["날짜"].dt.month == m)]
    if sub.empty:
        return 0, 0, 0, 0, 0, 0
    pnl = sub[~sub["카테고리"].isin(NON_PNL_CATEGORIES)]
    inc = pnl[pnl["유형"] == "입금"]["금액"].sum()
    exp = pnl[pnl["유형"] == "출금"]["금액"].sum()
    sal = sub[sub["카테고리"] == "근로소득"]["금액"].sum()
    fixed = pnl[(pnl["유형"] == "출금") & pnl["카테고리"].isin(FIXED_CATEGORIES)]["금액"].sum()
    variable = exp - fixed
    return inc, exp, inc - exp, sal, fixed, variable


def _net_worth_snapshot(df_src, year=None, month=None):
    """현 시점 순자산 스냅샷.

    자산(+): IBK 최근 잔액, 카뱅 최근 잔액(마통이면 음수)
    부채(−): 어머니 차입금 누적 순증, 이번달 카드사용 누계(다음달 청구)
    Returns: dict — ibk, kakao, mom_debt, card_debt, net_worth
    """
    snap = {"ibk": None, "kakao": None, "mom_debt": 0, "card_debt": 0, "net_worth": None}
    if df_src is None or df_src.empty:
        return snap

    if "잔액" in df_src.columns:
        for src in ("IBK기업은행", "카카오뱅크"):
            rows = df_src[(df_src["출처"] == src) & df_src["잔액"].notna()]
            if not rows.empty:
                latest = rows.sort_values("날짜", ascending=False).iloc[0]
                key = "ibk" if src == "IBK기업은행" else "kakao"
                snap[key] = int(latest["잔액"])

    # 어머니차입금 누적 (전체 기간 시트 기준)
    mom = df_src[df_src["카테고리"] == "어머니차입금"]
    snap["mom_debt"] = int(
        mom[mom["유형"] == "입금"]["금액"].sum() - mom[mom["유형"] == "출금"]["금액"].sum()
    )

    # 이번달 카드 사용 누계 (다음달 청구 예고) — 손익에 포함되는 카드 출금
    if year and month:
        card_use = df_src[
            (df_src["날짜"].dt.year == year)
            & (df_src["날짜"].dt.month == month)
            & (df_src["출처"].isin(CARD_SOURCES))
            & (df_src["유형"] == "출금")
            & (~df_src["카테고리"].isin(NON_PNL_CATEGORIES))
        ]
        snap["card_debt"] = int(card_use["금액"].sum())

    # 순자산 = 잔액합 − 어머니부채 − 카드부채
    assets = (snap["ibk"] or 0) + (snap["kakao"] or 0)
    snap["net_worth"] = assets - snap["mom_debt"] - snap["card_debt"]
    return snap


def forecast_cash_flow(
    df_src, months_ahead: int = 3, history_months: int = 3,
) -> pd.DataFrame:
    """최근 history_months 평균 기반으로 다음 months_ahead 개월 현금흐름 예측.

    예상 손익 = 평균 수입 − 평균 지출 (NON_PNL 제외)
    카뱅 마통 잔액은 현재 잔액 + 누적 손익으로 시뮬레이션.

    Returns: DataFrame [월, 예상수입, 예상지출, 예상손익, 누적손익, 예상카뱅잔액]
    """
    if df_src is None or df_src.empty:
        return pd.DataFrame()
    pnl = df_src[~df_src["카테고리"].isin(NON_PNL_CATEGORIES)].copy()
    if pnl.empty:
        return pd.DataFrame()
    pnl["월"] = pnl["날짜"].dt.to_period("M")
    by_month = pnl.groupby(["월", "유형"])["금액"].sum().unstack(fill_value=0)
    if "입금" not in by_month.columns:
        by_month["입금"] = 0
    if "출금" not in by_month.columns:
        by_month["출금"] = 0

    recent = by_month.tail(history_months)
    if recent.empty:
        return pd.DataFrame()
    avg_inc = float(recent["입금"].mean())
    avg_exp = float(recent["출금"].mean())
    avg_pnl = avg_inc - avg_exp

    # 카뱅 현재 잔액
    kk_rows = df_src[(df_src["출처"] == "카카오뱅크") & df_src["잔액"].notna()] \
        if "잔액" in df_src.columns else pd.DataFrame()
    cur_kk = int(kk_rows.sort_values("날짜").iloc[-1]["잔액"]) if not kk_rows.empty else None

    last_month = by_month.index.max()
    rows = []
    cum_pnl = 0
    for i in range(1, months_ahead + 1):
        future = last_month + i
        cum_pnl += avg_pnl
        rows.append({
            "월": str(future),
            "예상수입": int(avg_inc),
            "예상지출": int(avg_exp),
            "예상손익": int(avg_pnl),
            "누적손익": int(cum_pnl),
            "예상카뱅잔액": int(cur_kk + cum_pnl) if cur_kk is not None else None,
        })
    return pd.DataFrame(rows)


# 손익(P&L) 정제: 자기자금 이동·부채 변동은 손익에서 제외
# 카드사용 누계·차트용 (선택된 카테고리 필터 적용된 df 기준)
df_pnl = df[~df["카테고리"].isin(NON_PNL_CATEGORIES)] if not df.empty else df
# 상단 메트릭은 카테고리 필터 무시하고 그 달 전체 손익 (df_all 기준)
income, expense, balance, salary, fixed_cost, variable_cost = _month_pnl(df_all, year, month) \
    if not df_all.empty else (0, 0, 0, 0, 0, 0)

# 전월 (Month-over-Month)
prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
prev_inc, prev_exp, prev_bal, prev_sal, prev_fix, prev_var = _month_pnl(df_all, prev_y, prev_m) \
    if not df_all.empty else (0, 0, 0, 0, 0, 0)


def _delta(curr, prev, money=True):
    """MoM 증감 표시 문자열 (st.metric의 delta 인자용)."""
    if prev == 0:
        return None
    diff = curr - prev
    pct = (diff / abs(prev)) * 100
    sign = "+" if diff >= 0 else ""
    if money:
        return f"{sign}{diff:,.0f}원 ({sign}{pct:.1f}%)"
    return f"{sign}{diff:,.0f} ({sign}{pct:.1f}%)"


col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric(
        "💼 근로소득", f"{salary:,.0f}원",
        delta=_delta(salary, prev_sal),
        help=f"전월({prev_y}-{prev_m:02d}) 대비 증감",
    )
with col2:
    st.metric(
        "💚 총 수입", f"{income:,.0f}원",
        delta=_delta(income, prev_inc),
    )
with col3:
    st.metric(
        "❤️ 총 지출", f"{expense:,.0f}원",
        delta=_delta(expense, prev_exp),
        delta_color="inverse",  # 지출 증가는 빨강
    )
with col4:
    st.metric(
        "💙 순손익", f"{balance:+,.0f}원",
        delta=_delta(balance, prev_bal),
    )
with col5:
    # 저축률 = (근로소득 - 지출) / 근로소득
    if salary > 0:
        savings_rate = (salary - expense) / salary * 100
        prev_rate = (prev_sal - prev_exp) / prev_sal * 100 if prev_sal > 0 else 0
        rate_delta = f"{savings_rate - prev_rate:+.1f}%p" if prev_sal > 0 else None
    else:
        savings_rate = 0
        rate_delta = None
    st.metric(
        "💰 저축률",
        f"{savings_rate:.1f}%",
        delta=rate_delta,
        help="근로소득 대비 (근로소득 − 지출) 비율. 음수면 근로소득만으로 부족해 적자.",
    )

st.caption(
    "💡 손익은 자기자금 이동(어머니차입금·자기이체)과 카드대금 자동이체(부채청산)를 제외하고 계산합니다. "
    "전월 대비 증감은 같은 정제 기준."
)

# ── 🧹 시트 정합성 점검 결과 (사이드바 버튼으로 트리거) ──
if st.session_state.get("_invalid_rows"):
    invalid = st.session_state["_invalid_rows"]
    with st.expander(f"🧹 시트 정합성 점검 — 형식 이상 {len(invalid)}건", expanded=True):
        st.dataframe(pd.DataFrame(invalid), use_container_width=True, hide_index=True)
        st.caption(
            "💡 '행' 컬럼의 번호로 시트에서 직접 찾아 수정/삭제 가능. "
            "수정 후 '🔄 데이터 새로고침'을 누르세요."
        )

# ── 📋 순자산 스냅샷 (자산 − 부채) ───────────────────
snap = _net_worth_snapshot(df_all, year, month) if not df_all.empty else None
if snap and (snap["ibk"] is not None or snap["kakao"] is not None or snap["mom_debt"] or snap["card_debt"]):
    st.markdown("##### 📋 순자산 스냅샷")
    assets = (snap["ibk"] or 0) + (snap["kakao"] or 0)
    liabilities = snap["mom_debt"] + snap["card_debt"]
    nw_col1, nw_col2, nw_col3, nw_col4 = st.columns(4)
    with nw_col1:
        parts = []
        if snap["ibk"] is not None:
            parts.append(f"IBK {snap['ibk']:+,}")
        if snap["kakao"] is not None:
            parts.append(f"카뱅 {snap['kakao']:+,}")
        st.metric(
            "💵 자산 (잔액 합)",
            f"{assets:+,.0f}원",
            help=" / ".join(parts) if parts else "잔액 데이터 없음 — 재업로드 필요",
        )
    with nw_col2:
        st.metric(
            "📕 부채 (누적)",
            f"−{liabilities:,.0f}원" if liabilities else "0원",
            help=f"어머니차입금 {snap['mom_debt']:+,}원 + 이번달 카드사용 {snap['card_debt']:,}원",
            delta_color="off",
        )
    with nw_col3:
        nw = snap["net_worth"]
        st.metric(
            "🏛️ 순자산",
            f"{nw:+,.0f}원",
            help="자산 − 부채. 음수면 부채가 자산보다 큼.",
        )
    with nw_col4:
        if salary > 0:
            months_to_payoff = abs(min(nw, 0)) / salary if nw < 0 else 0
            st.metric(
                "🗓️ 순자산 회복(월)",
                f"{months_to_payoff:.1f}개월" if nw < 0 else "✅ 흑자",
                help="현재 월급만으로 순자산이 0이 되려면 몇 개월 필요한지 (현 부채 ÷ 월급)",
            )
    st.caption(
        "💡 카뱅 잔액은 마통이라 음수. 카드 부채는 이번달 사용 누계(다음달 청구)."
    )

# ── 고정비 vs 변동비 + 연간 환산 ─────────────────────
col_fv, col_yr = st.columns([2, 1])
with col_fv:
    if expense > 0:
        st.markdown("##### 🧱 고정비 vs 변동비")
        fixed_pct = fixed_cost / expense * 100
        var_pct = variable_cost / expense * 100
        fv_df = pd.DataFrame({
            "구분": ["고정비", "변동비"],
            "금액": [fixed_cost, variable_cost],
            "비율": [f"{fixed_pct:.1f}%", f"{var_pct:.1f}%"],
        })
        st.dataframe(
            fv_df.assign(금액=fv_df["금액"].apply(lambda x: f"{x:,.0f}원")),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            f"고정비 = {' / '.join(sorted(FIXED_CATEGORIES))}"
        )
with col_yr:
    # 연간 환산: 이번 달 기준 × 12 + 시트 전체 평균 × 12 두 가지
    st.markdown("##### 📅 연간 환산 (12개월)")
    if salary > 0:
        st.metric("근로소득 (월×12)", f"{salary * 12:,.0f}원")
    if balance != 0:
        st.metric("순손익 (월×12)", f"{balance * 12:+,.0f}원",
                  delta_color="off")

# ── 💰 카테고리 예산 vs 실제 ───────────────────────
budget = load_budget()
if budget and not df_all.empty:
    month_exp = df_all[
        (df_all["날짜"].dt.year == year)
        & (df_all["날짜"].dt.month == month)
        & (df_all["유형"] == "출금")
        & ~df_all["카테고리"].isin(NON_PNL_CATEGORIES)
    ]
    if not month_exp.empty:
        st.markdown("##### 💰 카테고리 예산 vs 실제")
        actuals = month_exp.groupby("카테고리")["금액"].sum().to_dict()
        rows = []
        over_count = 0
        for cat, limit in sorted(budget.items(), key=lambda x: -x[1]):
            actual = int(actuals.get(cat, 0))
            pct = (actual / limit * 100) if limit > 0 else 0
            over = actual > limit
            if over:
                over_count += 1
            rows.append({
                "카테고리": cat,
                "예산": f"{limit:,.0f}원",
                "실제": f"{actual:,.0f}원",
                "진행률": f"{pct:.0f}%",
                "상태": "🔴 초과" if over else ("🟡 80%+" if pct >= 80 else "🟢"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if over_count:
            st.warning(f"⚠️ {over_count}개 카테고리가 이번달 예산을 초과했습니다.")
        st.caption(
            "💡 예산은 시트의 '예산' 워크시트에서 직접 편집. "
            "처음 실행 시 디폴트 예산이 자동 생성됩니다."
        )

# ── 💳 카드사용 누계 — 다음달 청구 예고 ─────────────
if not df.empty and "출처" in df.columns:
    card_use = df[
        df["출처"].isin(CARD_SOURCES)
        & (df["유형"] == "출금")
        & (~df["카테고리"].isin(NON_PNL_CATEGORIES))
    ]
    if not card_use.empty:
        st.subheader("💳 이번달 카드사용 누계 (다음달 청구 예고)")
        rows = []
        for src in sorted(card_use["출처"].unique()):
            sub = card_use[card_use["출처"] == src]
            rows.append({
                "카드": src,
                "이번달 사용액": f"{sub['금액'].sum():,.0f}원",
                "건수": len(sub),
                "최고 1건": f"{sub['금액'].max():,.0f}원",
            })
        total = card_use["금액"].sum()
        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        with col_b:
            st.metric(
                label="다음달 청구 합계 예상",
                value=f"{total:,.0f}원",
                help="이용일 기준 이번달 카드사용 누계. 실제 청구는 카드사별 마감일에 따라 일부 차이가 있을 수 있습니다.",
            )

# ── 🔮 3개월 현금흐름 예측 ─────────────────────────
if not df_all.empty:
    forecast = forecast_cash_flow(df_all, months_ahead=3, history_months=3)
    if not forecast.empty:
        st.subheader("🔮 다음 3개월 현금흐름 예측")
        show_fc = forecast.copy()
        for c in ("예상수입", "예상지출", "예상손익", "누적손익"):
            show_fc[c] = show_fc[c].apply(lambda x: f"{x:+,.0f}원" if c in ("예상손익", "누적손익") else f"{x:,.0f}원")
        if "예상카뱅잔액" in show_fc.columns and show_fc["예상카뱅잔액"].notna().any():
            show_fc["예상카뱅잔액"] = show_fc["예상카뱅잔액"].apply(
                lambda x: f"{x:+,.0f}원" if pd.notna(x) else "-"
            )
        st.dataframe(show_fc, use_container_width=True, hide_index=True)
        last = forecast.iloc[-1]
        if last["누적손익"] < 0:
            st.warning(
                f"⚠️ 최근 3개월 추세대로면 3개월 후 누적 적자 {abs(last['누적손익']):,.0f}원. "
                f"카뱅 마통이 더 빠지거나 어머니께 추가 차입 필요."
            )
        elif last["누적손익"] > 0:
            st.success(
                f"✅ 최근 3개월 추세대로면 3개월간 누적 흑자 {last['누적손익']:,.0f}원 예상."
            )
        st.caption(
            "💡 최근 3개월 평균 수입·지출 기반 단순 외삽. 일회성 수입(연말정산·상여)이 빠지면 오차 가능."
        )

# ── 🏷️ 카테고리 수동 매핑 (앱에서 직접 편집) ──────────
with st.expander("🏷️ 카테고리 수동 매핑 (특정 가맹점 일괄 변경)"):
    st.caption(
        "내역을 정확히 입력하고 카테고리를 선택하면, 시트의 동일 내역 행 전체에 적용됩니다. "
        "다음 일괄 재분류 시 자동 학습 시스템이 이 매핑을 기억합니다."
    )
    # 후보 내역: 시트의 출금 중 카테고리=기타 + 빈도 ≥ 2
    suggestions = []
    if not df_all.empty:
        candidates = df_all[
            (df_all["유형"] == "출금") & (df_all["카테고리"] == "기타")
        ]["내역"].value_counts()
        suggestions = candidates[candidates >= 2].index.tolist()[:30]
    col_m, col_c, col_b = st.columns([2, 2, 1])
    with col_m:
        if suggestions:
            picked = st.selectbox(
                "내역 선택 (기타로 분류된 빈도 2+)",
                ["(직접 입력)"] + suggestions,
                key="manual_merch_select",
            )
            manual_merch = (
                st.text_input("내역 직접 입력", key="manual_merch_input")
                if picked == "(직접 입력)" else picked
            )
        else:
            manual_merch = st.text_input("내역 (정확 일치)", placeholder="예: 아이딜컨스트럭션")
    with col_c:
        all_cats = sorted(
            set(CATEGORY_KEYWORDS.keys()) | NON_PNL_CATEGORIES
            | {"기타", "수입", "근로소득", "기타수입"}
        )
        manual_cat = st.selectbox("카테고리", all_cats, key="manual_cat_select")
    with col_b:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("적용", key="manual_apply"):
            if manual_merch and manual_merch.strip():
                try:
                    n = apply_manual_category(manual_merch, manual_cat)
                    if n:
                        st.success(f"✅ '{manual_merch}' → {manual_cat} ({n}건 업데이트)")
                        st.cache_data.clear()
                    else:
                        st.info("변경 없음 (이미 같은 카테고리이거나 매칭 행 없음)")
                except Exception as e:
                    st.error(f"오류: {e}")
            else:
                st.warning("내역을 입력하세요")

# ── ⚠️ 이상치 알림 (이번달 카테고리 중앙값 대비 3배 초과) ──
if not df_all.empty:
    outliers = detect_outliers(df_all, year, month, threshold_ratio=3.0)
    if not outliers.empty:
        st.subheader("⚠️ 이상치 알림")
        show_o = outliers.copy()
        show_o["금액"] = show_o["금액"].apply(lambda x: f"{x:,.0f}원")
        show_o["카테고리 중앙값"] = show_o["카테고리 중앙값"].apply(lambda x: f"{x:,.0f}원")
        show_o["배수"] = show_o["배수"].apply(lambda x: f"×{x}")
        st.dataframe(show_o, use_container_width=True, hide_index=True)
        st.caption(
            "💡 같은 카테고리의 시트 전체 중앙값 대비 3배 이상 큰 거래. "
            "큰 결제·할부 1회분이거나 카테고리 오분류 신호."
        )

# ── 💳 카드 청구 ↔ 사용 매칭 (시트 전체 기간) ─────────
if not df_all.empty:
    match_df = match_card_charges_to_usage(df_all, window_days=35)
    if not match_df.empty:
        st.subheader("💳 카드 청구 ↔ 사용 매칭")
        show = match_df.copy()
        usage_col = [c for c in show.columns if c.startswith("사용액")][0]
        show["청구액"] = show["청구액"].apply(lambda x: f"{x:,.0f}원")
        show[usage_col] = show[usage_col].apply(lambda x: f"{x:,.0f}원")
        show["차이"] = match_df["차이"].apply(
            lambda x: f"{x:+,.0f}원 {'⚠️' if abs(x) > 200000 else ''}"
        )
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption(
            "💡 청구액(IBK에서 카드사로 빠진 자동이체) vs 직전 35일 카드 사용 합계. "
            "차이가 ±20만원 이상이면 ⚠️ 표시 — 장기할부·이월·환불 점검."
        )

st.markdown("<br>", unsafe_allow_html=True)

# ── 차트 ──────────────────────────────────────────────
if not df.empty:
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("📊 카테고리별 지출")
        expense_df = (
            df_pnl[df_pnl["유형"] == "출금"]
            .groupby("카테고리")["금액"].sum().reset_index()
        )
        if not expense_df.empty:
            fig_pie = px.pie(
                expense_df, values="금액", names="카테고리",
                color_discrete_sequence=px.colors.qualitative.Set3,
                hole=0.4,
            )
            fig_pie.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=320)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("지출 데이터 없음")

    with col_right:
        st.subheader("📈 일별 수입/지출")
        daily = df_pnl.groupby(["날짜", "유형"])["금액"].sum().reset_index()
        if not daily.empty:
            fig_bar = px.bar(
                daily, x="날짜", y="금액", color="유형",
                color_discrete_map={"입금": "#38ef7d", "출금": "#f45c43"},
                barmode="group",
            )
            fig_bar.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=320,
                                   legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("데이터 없음")

    # 출처별 지출
    st.subheader("🏦 출처별 지출")
    source_df = (
        df_pnl[df_pnl["유형"] == "출금"]
        .groupby("출처")["금액"].sum().reset_index()
    )
    if not source_df.empty:
        fig_source = px.bar(
            source_df.sort_values("금액", ascending=True),
            x="금액", y="출처", orientation="h",
            color="금액",
            color_continuous_scale="Reds",
        )
        fig_source.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=200,
                                  coloraxis_showscale=False)
        st.plotly_chart(fig_source, use_container_width=True)

    # ── 손익 외 흐름 (자기자금 이동·부채 변동) ──────────
    df_non_pnl = df[df["카테고리"].isin(NON_PNL_CATEGORIES)]
    if not df_non_pnl.empty:
        st.subheader("🔁 손익 외 흐름 (참고)")
        rows = []
        for cat in ["어머니차입금", "부채청산", "자기이체", "환불/캐시백"]:
            sub = df_non_pnl[df_non_pnl["카테고리"] == cat]
            if sub.empty:
                continue
            in_sum = sub[sub["유형"] == "입금"]["금액"].sum()
            out_sum = sub[sub["유형"] == "출금"]["금액"].sum()
            rows.append({
                "카테고리": cat,
                "입금": f"{in_sum:,.0f}원",
                "출금": f"{out_sum:,.0f}원",
                "순흐름": f"{in_sum - out_sum:+,.0f}원",
                "건수": len(sub),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "💡 어머니차입금: 농협 입금=빌림(부채↑), 출금=상환(부채↓). "
            "부채청산: 카드대금 자동이체. 자기이체: 본인 계좌 간 이동. "
            "이 항목들은 위 손익 합계에서 제외됩니다."
        )

    # ── 💛 카뱅 마통 잔액 추이 + 🔮 3개월 예측 (점선) ─────
    if "잔액" in df_all.columns:
        kk_hist = df_all[(df_all["출처"] == "카카오뱅크") & df_all["잔액"].notna()].copy()
        if len(kk_hist) >= 2:
            st.subheader("💛 카카오뱅크 마통 잔액 추이 (+ 3개월 예측)")
            kk_hist = kk_hist.sort_values("날짜")
            # 실측 area
            fig_bal = px.area(
                kk_hist, x="날짜", y="잔액",
                color_discrete_sequence=["#f59e0b"],
            )
            # 예측 점선 — forecast의 예상카뱅잔액
            forecast_chart = forecast_cash_flow(df_all, months_ahead=3, history_months=3)
            if not forecast_chart.empty and forecast_chart["예상카뱅잔액"].notna().any():
                last_real_date = kk_hist["날짜"].max()
                last_real_bal = int(kk_hist.iloc[-1]["잔액"])
                pred_dates = [last_real_date]
                pred_bals = [last_real_bal]
                for _, r in forecast_chart.iterrows():
                    period = pd.Period(r["월"], freq="M")
                    pred_dates.append(period.to_timestamp("M"))  # 월말
                    pred_bals.append(int(r["예상카뱅잔액"]))
                fig_bal.add_scatter(
                    x=pred_dates, y=pred_bals,
                    mode="lines+markers",
                    line=dict(dash="dash", color="#dc2626", width=2),
                    name="3개월 예측",
                )
            fig_bal.add_hline(y=0, line_dash="dash", line_color="gray",
                              annotation_text="0원", annotation_position="right")
            fig_bal.update_layout(
                margin=dict(t=10, b=0, l=0, r=0), height=280,
                yaxis_title="잔액(원)",
                legend=dict(orientation="h", y=1.05),
            )
            st.plotly_chart(fig_bal, use_container_width=True)
            start_bal = int(kk_hist.iloc[0]["잔액"])
            end_bal = int(kk_hist.iloc[-1]["잔액"])
            diff = end_bal - start_bal
            st.caption(
                f"실측: 시작 {start_bal:+,}원 → 최근 {end_bal:+,}원 "
                f"({'마통 개선' if diff > 0 else '마통 악화'} {abs(diff):,}원). "
                f"점선은 최근 3개월 평균 손익 외삽."
            )

    # ── 🏆 TOP 가맹점 + 📈 카테고리별 월별 추이 (시트 전체) ──
    if not df_all.empty:
        tab_top, tab_trend = st.tabs(["🏆 TOP 가맹점", "📈 카테고리 월별 추이"])
        with tab_top:
            pnl_all = df_all[
                ~df_all["카테고리"].isin(NON_PNL_CATEGORIES)
                & (df_all["유형"] == "출금")
            ]
            if pnl_all.empty:
                st.info("출금 데이터 없음")
            else:
                top10 = (
                    pnl_all.groupby(["내역", "카테고리"])["금액"]
                    .agg(["count", "sum"])
                    .sort_values("sum", ascending=False)
                    .head(10)
                    .reset_index()
                    .rename(columns={"count": "건수", "sum": "합계"})
                )
                top10["합계"] = top10["합계"].apply(lambda x: f"{x:,.0f}원")
                st.dataframe(top10, use_container_width=True, hide_index=True)
                st.caption("시트 전체 기간 출금(손익 포함분) 기준 TOP 10")
        with tab_trend:
            pnl_all = df_all[~df_all["카테고리"].isin(NON_PNL_CATEGORIES)].copy()
            pnl_all["월"] = pnl_all["날짜"].dt.strftime("%Y-%m")
            exp_trend = (
                pnl_all[pnl_all["유형"] == "출금"]
                .groupby(["월", "카테고리"])["금액"].sum().reset_index()
            )
            if exp_trend.empty:
                st.info("추이 데이터 없음")
            else:
                fig_trend = px.line(
                    exp_trend, x="월", y="금액", color="카테고리",
                    markers=True,
                )
                fig_trend.update_layout(
                    margin=dict(t=10, b=0, l=0, r=0), height=380,
                    yaxis_title="월별 지출",
                )
                st.plotly_chart(fig_trend, use_container_width=True)
                st.caption("카테고리별 월별 지출 추이 — 손익 포함분만")

    # ── 거래 내역 테이블 ──────────────────────────────
    st.subheader("📋 거래 내역")

    display_df = df[["날짜", "출처", "유형", "금액", "내역", "카테고리"]].copy()
    display_df["날짜"] = display_df["날짜"].dt.strftime("%Y-%m-%d")
    display_df["금액"] = display_df["금액"].apply(lambda x: f"{x:,.0f}원")

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "유형": st.column_config.TextColumn(width="small"),
            "출처": st.column_config.TextColumn(width="small"),
            "카테고리": st.column_config.TextColumn(width="small"),
        }
    )

else:
    st.info(f"📭 {selected_month} 데이터가 없어요. 이메일 파싱이 실행되면 자동으로 채워집니다.")

# ── 현대카드 수동 업로드 ──────────────────────────────
st.markdown("---")
st.subheader("💳 현대카드 내역 업로드")

col_up, col_info = st.columns([1, 2])
with col_up:
    uploaded = st.file_uploader("현대카드 Excel/CSV/HTML 파일", type=["xlsx", "xls", "csv", "html"])
with col_info:
    st.markdown("""
    **현대카드 내역 내보내기 방법:**
    1. 현대카드 앱 → 이용내역
    2. 우측 상단 다운로드 아이콘
    3. Excel 파일 저장
    4. 여기에 업로드 → 미리보기 확인 → **시트에 저장**
    """)

if uploaded:
    try:
        parsed = parse_hyundai_file(uploaded)
    except Exception as e:
        st.error(f"파일 파싱 오류: {e}")
        parsed = None

    if parsed is not None:
        if parsed.empty:
            st.warning("거래 내역을 찾지 못했어요. 파일 형식을 확인해주세요.")
        else:
            st.success(f"✅ {len(parsed)}건 파싱됨 — 합계 {parsed['금액'].sum():,.0f}원")
            _maybe_autosave(parsed, "현대카드", uploaded)
            edited = st.data_editor(
                parsed,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="hyundai_editor",
            )
            if st.button("📤 Google Sheets에 저장", type="primary", key="hyundai_save"):
                try:
                    transactions = edited.to_dict(orient="records")
                    added = append_transactions_to_sheet(transactions)
                    if added:
                        st.success(f"✅ {added}건 시트에 저장 완료 (중복 {len(transactions) - added}건 제외)")
                        st.cache_data.clear()
                    else:
                        st.info(f"중복 {len(transactions)}건 — 새로 추가된 거래 없음")
                except Exception as e:
                    st.error(f"저장 오류: {e}")


# ── IBK 기업은행 입출금 통장 업로드 ────────────────────
st.markdown("---")
st.subheader("🏦 기업은행 입출금 내역 업로드")

col_ibk_up, col_ibk_info = st.columns([1, 2])
with col_ibk_up:
    uploaded_ibk = st.file_uploader(
        "IBK 입출금 거래내역 (.xls)", type=["xls", "xlsx", "html"], key="ibk_upload"
    )
with col_ibk_info:
    st.markdown("""
    **IBK기업은행 거래내역 내보내기 방법:**
    1. i-ONE 뱅크 웹 / 인터넷뱅킹 → 조회 → 거래내역조회(입출식)
    2. 조회 기간 설정 후 검색
    3. 우측 "엑셀 다운로드" 클릭 → `.xls` 파일 저장
    4. 여기에 업로드 → 미리보기 확인 → **시트에 저장**
    """)

if uploaded_ibk:
    try:
        parsed_ibk = parse_ibk_account_file(uploaded_ibk)
    except Exception as e:
        st.error(f"파일 파싱 오류: {e}")
        parsed_ibk = None

    if parsed_ibk is not None:
        if parsed_ibk.empty:
            st.warning("거래 내역을 찾지 못했어요. 파일 형식을 확인해주세요.")
        else:
            out_sum = parsed_ibk[parsed_ibk["유형"] == "출금"]["금액"].sum()
            in_sum = parsed_ibk[parsed_ibk["유형"] == "입금"]["금액"].sum()
            st.success(
                f"✅ {len(parsed_ibk)}건 파싱됨 — 출금 {out_sum:,.0f}원 / 입금 {in_sum:,.0f}원"
            )
            st.caption("⚠️ 같은 거래가 BC카드 명세서·이메일 알림에도 있을 수 있어요. 중복 가능성 확인 후 저장하세요.")
            _maybe_autosave(parsed_ibk, "IBK", uploaded_ibk)
            edited_ibk = st.data_editor(
                parsed_ibk,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="ibk_editor",
            )
            if st.button("📤 Google Sheets에 저장", type="primary", key="ibk_save"):
                try:
                    transactions = edited_ibk.to_dict(orient="records")
                    added = append_transactions_to_sheet(transactions)
                    if added:
                        st.success(f"✅ {added}건 시트에 저장 완료 (중복 {len(transactions) - added}건 제외)")
                        st.cache_data.clear()
                    else:
                        st.info(f"중복 {len(transactions)}건 — 새로 추가된 거래 없음")
                except Exception as e:
                    st.error(f"저장 오류: {e}")


# ── 카카오뱅크 입출금 내역 업로드 ────────────────────
st.markdown("---")
st.subheader("💛 카카오뱅크 거래내역 업로드")

col_kk_up, col_kk_info = st.columns([1, 2])
with col_kk_up:
    uploaded_kakao = st.file_uploader(
        "카카오뱅크 거래내역 (.xlsx)", type=["xlsx"], key="kakao_upload"
    )
    kakao_pw = st.text_input(
        "비밀번호 (잠긴 파일이면 입력)",
        type="password",
        key="kakao_pw",
        placeholder="예: 생년월일 6자리",
    )
with col_kk_info:
    st.markdown("""
    **카카오뱅크 거래내역 내보내기 방법:**
    1. 카카오뱅크 앱 → 계좌 선택 → 거래내역
    2. 우측 상단 메뉴 → **거래내역 내보내기**
    3. 기간/형식(Excel) 선택 후 비밀번호 설정
    4. 받은 `.xlsx` 파일 + 비밀번호를 여기에 입력
    """)

if uploaded_kakao:
    try:
        parsed_kakao = parse_kakaobank_file(uploaded_kakao, password=kakao_pw or None)
    except Exception as e:
        st.error(f"파일 파싱 오류: {e}")
        parsed_kakao = None

    if parsed_kakao is not None:
        if parsed_kakao.empty:
            st.warning("거래 내역을 찾지 못했어요. 파일 형식을 확인해주세요.")
        else:
            out_sum = parsed_kakao[parsed_kakao["유형"] == "출금"]["금액"].sum()
            in_sum = parsed_kakao[parsed_kakao["유형"] == "입금"]["금액"].sum()
            st.success(
                f"✅ {len(parsed_kakao)}건 파싱됨 — 출금 {out_sum:,.0f}원 / 입금 {in_sum:,.0f}원"
            )
            st.caption("⚠️ 카카오뱅크 자동이체로 결제된 카드 거래가 카드 명세서에도 잡힐 수 있어요. 중복 가능성 확인 후 저장하세요.")
            _maybe_autosave(parsed_kakao, "카카오뱅크", uploaded_kakao)
            edited_kakao = st.data_editor(
                parsed_kakao,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="kakao_editor",
            )
            if st.button("📤 Google Sheets에 저장", type="primary", key="kakao_save"):
                try:
                    transactions = edited_kakao.to_dict(orient="records")
                    added = append_transactions_to_sheet(transactions)
                    if added:
                        st.success(f"✅ {added}건 시트에 저장 완료 (중복 {len(transactions) - added}건 제외)")
                        st.cache_data.clear()
                    else:
                        st.info(f"중복 {len(transactions)}건 — 새로 추가된 거래 없음")
                except Exception as e:
                    st.error(f"저장 오류: {e}")
