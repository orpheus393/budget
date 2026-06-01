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

# 입금/출금 양방향 매칭 카테고리 (부호와 무관하게 키워드만으로 분류)
BIDIRECTIONAL_RULES = [
    # (카테고리, 키워드 목록) — 위에서부터 first-match
    ("어머니차입금", ["임영재"]),
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


def guess_category(merchant: str, tx_type: str) -> str:
    """가맹점/내역 텍스트와 거래 유형(입금/출금)으로 카테고리 추론.

    우선순위:
    1) 양방향 규칙(어머니차입금/부채청산) — 입출금 무관 키워드 일치
    2) 입금: 환불·캐시백 키워드 / 그 외는 키워드 매칭 후 fallback "수입"
    3) 출금: 키워드 매칭, 매칭 없으면 "기타"
    """
    text = (merchant or "")
    text_lower = text.lower()

    # 1) 양방향 (어머니차입금 / 부채청산)
    for cat, kws in BIDIRECTIONAL_RULES:
        for kw in kws:
            if kw.lower() in text_lower:
                return cat

    # 2) 입금
    if tx_type == "입금":
        if any(k in text for k in ["환불", "취소", "캐시백"]):
            return "환불/캐시백"
        # 입금 쪽 키워드 매칭 (근로소득/기타수입/개인송금)
        for cat in ("근로소득", "기타수입", "개인송금"):
            for kw in CATEGORY_KEYWORDS.get(cat, []):
                if kw.lower() in text_lower:
                    return cat
        return "수입"

    # 3) 출금
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in ("근로소득", "기타수입"):
            continue  # 입금 전용
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


def get_worksheet():
    creds_dict = dict(st.secrets["gcp_service_account"])
    sheet_id = st.secrets["GOOGLE_SHEET_ID"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(sheet_id)
    try:
        ws = sheet.worksheet("거래내역")
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet("거래내역", rows=10000, cols=10)
        ws.append_row(["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])
    return ws


# ── Google Sheets 로드 ────────────────────────────────
@st.cache_data(ttl=300)  # 5분 캐시
def load_data():
    try:
        ws = get_worksheet()
        data = ws.get_all_records()
        df = pd.DataFrame(data)

        if df.empty:
            return pd.DataFrame(columns=["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])

        df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
        df["금액"] = pd.to_numeric(df["금액"], errors="coerce").fillna(0)
        df = df.dropna(subset=["날짜"])
        df = df.sort_values("날짜", ascending=False)
        return df

    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        return pd.DataFrame(columns=["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])


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
            "카테고리": guess_category(merchant, tx_type),
            "원문": origin[:100],
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

        merchant = content or txkind or "카카오뱅크 거래"
        origin = " | ".join(p for p in ["카카오뱅크", txkind, memo] if p)

        txs.append({
            "날짜": dt.strftime("%Y-%m-%d"),
            "시간": dt.strftime("%H:%M"),
            "출처": "카카오뱅크",
            "유형": tx_type,
            "금액": amount,
            "내역": merchant[:50],
            "카테고리": guess_category(merchant, tx_type),
            "원문": origin[:100],
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
        new_rows.append([
            tx["날짜"], tx.get("시간", ""), tx["출처"], tx["유형"],
            tx["금액"], tx["내역"], tx["카테고리"], tx.get("원문", ""),
        ])

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    return len(new_rows)


def recategorize_all_rows() -> tuple[int, int]:
    """시트 모든 행에 guess_category를 다시 적용. (변경된 건수, 전체 건수) 반환."""
    ws = get_worksheet()
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return 0, 0
    header = rows[0]
    try:
        cat_idx = header.index("카테고리")
        type_idx = header.index("유형")
        merch_idx = header.index("내역")
    except ValueError:
        return 0, len(rows) - 1

    cat_col_letter = chr(ord("A") + cat_idx)
    updates = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= max(cat_idx, type_idx, merch_idx):
            continue
        old_cat = row[cat_idx]
        new_cat = guess_category(row[merch_idx], row[type_idx])
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
    return len(updates), len(rows) - 1


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
            changed, total = recategorize_all_rows()
            if changed:
                st.sidebar.success(f"✅ {total}건 중 {changed}건 카테고리 업데이트")
                st.cache_data.clear()
            else:
                st.sidebar.info(f"변경 없음 ({total}건 모두 최신)")
        except Exception as e:
            st.sidebar.error(f"재분류 오류: {e}")

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

st.sidebar.markdown("---")
st.sidebar.caption("현대카드 내역은 수동 업로드 필요")

# ── 메인 화면 ─────────────────────────────────────────
st.title(f"💰 {selected_month} 가계부")

# 손익(P&L) 정제: 자기자금 이동·부채 변동은 손익에서 제외
df_pnl = df[~df["카테고리"].isin(NON_PNL_CATEGORIES)] if not df.empty else df
income = df_pnl[df_pnl["유형"] == "입금"]["금액"].sum() if not df_pnl.empty else 0
expense = df_pnl[df_pnl["유형"] == "출금"]["금액"].sum() if not df_pnl.empty else 0
balance = income - expense
salary = (
    df[df["카테고리"] == "근로소득"]["금액"].sum() if not df.empty else 0
)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.markdown(f"""
    <div class="metric-card metric-income">
        <div class="metric-label">💼 근로소득</div>
        <div class="metric-value">{salary:,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col2:
    st.markdown(f"""
    <div class="metric-card metric-income">
        <div class="metric-label">💚 총 수입</div>
        <div class="metric-value">{income:,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col3:
    st.markdown(f"""
    <div class="metric-card metric-expense">
        <div class="metric-label">❤️ 총 지출</div>
        <div class="metric-value">{expense:,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col4:
    color = "metric-income" if balance >= 0 else "metric-expense"
    st.markdown(f"""
    <div class="metric-card {color}">
        <div class="metric-label">💙 순손익</div>
        <div class="metric-value">{balance:+,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col5:
    tx_count = len(df) if not df.empty else 0
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">📊 거래 건수</div>
        <div class="metric-value">{tx_count}건</div>
    </div>""", unsafe_allow_html=True)

st.caption(
    "💡 손익은 자기자금 이동(어머니차입금·자기이체)과 카드대금 자동이체(부채청산)를 제외하고 계산합니다."
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
