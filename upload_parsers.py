"""업로드 파일 파서 — 현대카드·IBK기업은행·카카오뱅크 거래내역 + 카테고리 분류.

app.py(대시보드 업로드)와 email_parser.py(메일 첨부 자동 적재)가 **같은 파서**를
쓰도록 app.py에서 분리했다. app.py는 모듈 최상위에서 대시보드를 그리므로
cron 경로에서 import할 수 없어, 순수 로직만 이 파일로 옮긴 것이다.
streamlit·plotly·gspread에 의존하지 않는다.
"""

import io
import os
import re
from datetime import datetime

import pandas as pd


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


def _get_owner_name() -> str:
    """본인 명의 계좌의 예금주명. 없으면 기본값.

    이 이름이 거래 내역의 '내역'에 나타나면 (1) 농협=어머니차입금
    (2) 카뱅·토스=자기이체로 분기됨.

    cron(streamlit 없는 경로)에서도 동작해야 하므로 OWNER_NAME 환경변수를
    먼저 보고, 없을 때만 streamlit secrets를 지연 import해서 읽는다.
    """
    name = os.environ.get("OWNER_NAME", "").strip()
    if name:
        return name
    try:
        import streamlit as st
        return st.secrets.get("OWNER_NAME", "") or "임영재"
    except Exception:
        return "임영재"


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

    # 1) 본인 명의 거래 (OWNER_NAME) — secrets로 사용자별 설정 가능
    owner = _get_owner_name()
    if owner and owner in text:
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


class _BytesUpload:
    """파싱 재시도를 위해 bytes를 반복해서 read()할 수 있는 업로드 어댑터."""

    def __init__(self, name: str, raw: bytes):
        self.name = name
        self._raw = raw

    def read(self):
        return self._raw


def parse_any_file(name: str, raw: bytes, password: str | None = None):
    """파일 내용을 보고 어느 기관 것인지 감지해 알맞은 파서로 처리.

    반환: (감지된 출처, DataFrame). 어느 파서에도 안 맞으면 ValueError.

    감지 순서 (내용 기반):
      · HTML 표: '거래일시'+'출금' 키워드 → IBK, '이용가맹점/이용일' → 현대카드
      · 암호화 OLE: 카카오뱅크 (세 기관 중 암호화 파일은 카뱅뿐)
      · xlsx/xls/csv: 헤더 키워드 시도 순서를 정해 순차 파싱
    """
    head = raw[:4096].decode("utf-8", errors="ignore")
    head_cp949 = raw[:4096].decode("cp949", errors="ignore")
    text_head = head + head_cp949
    is_html = any(m in head.lower() for m in ("<html", "<!doctype", "<table", "<script"))
    is_ole = raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # 암호화 xlsx 컨테이너
    is_zip = raw[:2] == b"PK"  # 일반 xlsx

    def _try_quiet(kind, fn):
        """형식 불일치는 조용히 건너뛰고 None 반환."""
        try:
            df = fn(_BytesUpload(name, raw))
            if df is not None and not df.empty:
                return kind, df
        except Exception:
            pass
        return None

    def kakao(u):
        return parse_kakaobank_file(u, password)

    if is_ole:
        # 세 기관 중 암호화 파일은 카카오뱅크뿐 — 비밀번호 요구/불일치
        # ValueError 메시지를 사용자에게 그대로 보여줘야 하므로 직접 호출.
        df = parse_kakaobank_file(_BytesUpload(name, raw), password)
        if df is None or df.empty:
            raise ValueError(f"'{name}' 해독은 됐지만 거래를 찾지 못했어요.")
        return "카카오뱅크", df

    if is_html:
        # IBK 마커가 뚜렷하면 IBK 먼저, 아니면 현대카드 먼저
        if "거래일시" in text_head or "거래내역조회" in text_head:
            order = [("IBK기업은행", parse_ibk_account_file), ("현대카드", parse_hyundai_file)]
        else:
            order = [("현대카드", parse_hyundai_file), ("IBK기업은행", parse_ibk_account_file)]
    elif is_zip:
        order = [("카카오뱅크", kakao), ("현대카드", parse_hyundai_file)]
    else:
        # csv 또는 기타 텍스트
        order = [("현대카드", parse_hyundai_file), ("IBK기업은행", parse_ibk_account_file)]

    for kind, fn in order:
        got = _try_quiet(kind, fn)
        if got:
            return got
    raise ValueError(
        f"'{name}' 형식을 인식하지 못했어요. 지원: 현대카드 Excel/CSV, "
        f"IBK 거래내역 .xls(HTML), 카카오뱅크 .xlsx"
    )


def find_bc_echo_rows(df_new: pd.DataFrame, df_all: pd.DataFrame) -> pd.Series:
    """업로드된 IBK 출금 중 BC카드(체크) 명세서와 겹치는 행(echo) 표시.

    체크카드는 사용 즉시 IBK 통장에서 빠져 같은 거래가 BC체크 명세서(자동)와
    IBK 업로드(수동) 양쪽에 기록됨 → 같은 (날짜, 금액)의 BC카드(체크) 행이
    시트에 이미 있으면 True. 저장 전 기본 제외 대상으로 쓴다.
    """
    idx = pd.Series(False, index=df_new.index)
    if df_new.empty or df_all is None or df_all.empty:
        return idx
    bc = df_all[(df_all["출처"] == "BC카드(체크)") & (df_all["유형"] == "출금")]
    if bc.empty:
        return idx
    bc_keys = {
        (d.strftime("%Y-%m-%d"), int(a))
        for d, a in zip(bc["날짜"], bc["금액"])
        if pd.notna(d) and pd.notna(a)
    }
    for i, row in df_new.iterrows():
        if row.get("출처") != "IBK기업은행" or row.get("유형") != "출금":
            continue
        try:
            key = (str(row["날짜"])[:10], int(row["금액"]))
        except (ValueError, TypeError):
            continue
        if key in bc_keys:
            idx.at[i] = True
    return idx
