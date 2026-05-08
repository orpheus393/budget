"""
email_parser.py
네이버 이메일에서 은행/카드 알림을 파싱해 Google Sheets에 저장하고,
파싱 대상 + 광고/쇼핑/뉴스레터/SNS 메일을 자동으로 폴더 이동합니다.
"""

import base64
import email
import html as html_module
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime

# ── 환경설정 ──────────────────────────────────────────
NAVER_EMAIL = os.environ.get("NAVER_EMAIL", "")
NAVER_APP_PW = os.environ.get("NAVER_APP_PW", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")
ENABLE_EMAIL_CLEANUP = os.environ.get("ENABLE_EMAIL_CLEANUP", "").lower() in ("1", "true", "yes")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "2"))
# BC카드 명세서 PDF 비밀번호 (생년월일 6자리). 미설정 시 PDF 파싱 건너뜀.
BC_PDF_PASSWORD = os.environ.get("BC_PDF_PASSWORD", "")
# 명세서를 한 번 파싱한 후에도 다음 달까지 LOOKBACK_HOURS 윈도우 밖에 머물 수 있으므로
# 명세서 메일 검색은 별도로 더 긴 윈도우(기본 35일)를 사용한다.
STATEMENT_LOOKBACK_DAYS = int(os.environ.get("STATEMENT_LOOKBACK_DAYS", "35"))

IMAP_HOST = "imap.naver.com"
IMAP_PORT = 993

# 분류된 이메일이 이동할 폴더 (UTF-8, IMAP UTF-7로 자동 인코딩)
PROCESSED_FOLDER = "가계부_처리완료"
AD_FOLDER = "가계부_광고"
SHOPPING_FOLDER = "가계부_쇼핑"
NEWSLETTER_FOLDER = "가계부_뉴스레터"
SNS_FOLDER = "가계부_SNS"
STATEMENT_FOLDER = "가계부_명세서"  # PDF 파싱 실패 시 보관용

# ── 발신자 → 출처 매핑 (결제/은행 알림만) ─────────────
SENDER_PATTERNS = {
    "BC카드": ["bcbill@bccard.com", "bccard.com"],
    "카카오뱅크": ["no-reply@mail.kakaobank.com", "kakaobank.com"],
    "현대카드": ["admin@hyundaicard.com", "hyundaicard.com"],
    "IBK기업은행": ["ibk.co.kr"],
    "네이버페이": ["naverpayadmin_noreply@navercorp.com"],
    "토스페이먼츠": ["bill@bill-mail.tosspayments.com", "tosspayments.com"],
    "나이스정보통신": ["nice_customer@nicepg.co.kr"],
    "헥토파이낸셜": ["noreply@hecto.co.kr"],
}

# 파싱 대상 이메일이 들어 있을 수 있는 폴더 (검색 대상)
IMAP_FOLDERS = [
    "INBOX",
    "&zK2tbAC3rLDIHA-",   # 청구/결제 폴더
    "&yPy7OA-|&vDDBoQ-",  # 네이버페이 등
]

# ── 비거래 이메일 분류 규칙 ───────────────────────────
NON_TX_CATEGORIES = [
    # (카테고리명, 폴더, sender 키워드, subject 키워드)
    ("쇼핑", SHOPPING_FOLDER,
     ["coupang.com", "11st.co.kr", "ssg.com", "gmarket", "auction.co.kr",
      "wemakeprice", "smartstore.naver.com", "ohou.se", "kurly.com",
      "musinsa", "29cm"],
     ["주문확인", "배송완료", "배송시작", "발송완료", "출고완료", "도착예정",
      "배송지연", "반품접수", "교환접수", "구매확정"]),
    ("SNS", SNS_FOLDER,
     ["instagram.com", "facebookmail.com", "linkedin.com", "twitter.com",
      "x.com", "tiktok.com", "youtube.com", "discord.com", "slack.com",
      "github.com"],
     []),
    ("뉴스레터", NEWSLETTER_FOLDER,
     ["substack.com", "mailchimp", "mailchi.mp", "newsletter@",
      "@news.", ".letter", "stibee.com", "maily.so"],
     ["뉴스레터", "newsletter", "주간", "월간", "weekly digest", "daily digest"]),
    ("광고", AD_FOLDER,
     [],
     ["(광고)", "[광고]", "광고)", "이벤트", "쿠폰", "할인", "프로모션",
      "특가", "혜택", "당첨", "추첨", "기획전", "세일", "초대권"]),
]

# ── 금액/유형 키워드 ──────────────────────────────────
# "1,000원 결제" / "결제 1,000원" 형태 우선, 그 다음 fallback
AMOUNT_NEAR_KEYWORD = re.compile(
    r"(?:(출금|이체|결제|승인|사용|입금|수신|환불|취소)\s*[:\s]?\s*([0-9,]+)\s*원"
    r"|([0-9,]+)\s*원\s*(?:이?\s*)?(출금|이체|결제|승인|사용|입금|수신|환불|취소))"
)
AMOUNT_FALLBACK = re.compile(r"([0-9,]+)\s*원")

# 무시해야 하는 컨텍스트 (잔액/한도/포인트 등)
AMOUNT_BLACKLIST_CONTEXT = [
    "잔액", "한도", "포인트", "마일리지", "적립", "누적", "총액", "현재잔액",
    "사용가능", "이용가능", "혜택받은", "할인받은", "사용한도", "이용한도",
]

INCOME_KEYWORDS = ["입금", "수신", "급여", "이자", "환급", "환불", "취소"]
EXPENSE_KEYWORDS = ["출금", "이체", "결제", "승인", "사용"]

CATEGORY_KEYWORDS = {
    "식비": ["식당", "음식", "카페", "커피", "배달", "맥도날드", "스타벅스", "버거킹",
             "편의점", "GS25", "CU", "세븐", "이마트24", "투썸", "메가", "공차",
             "BBQ", "교촌", "도미노", "피자"],
    "교통": ["택시", "버스", "지하철", "주유", "카카오택시", "티머니", "하이패스",
             "S-OIL", "SK에너지", "GS칼텍스", "현대오일뱅크", "철도", "코레일"],
    "쇼핑": ["쿠팡", "네이버", "G마켓", "옥션", "11번가", "이마트", "홈플러스",
             "코스트코", "마켓컬리", "올리브영", "다이소", "무신사"],
    "의료": ["병원", "약국", "의원", "클리닉", "치과", "한의원"],
    "통신": ["SKT", "KT", "LG", "통신", "인터넷", "헬로비전"],
    "구독": ["넷플릭스", "유튜브", "스포티파이", "왓챠", "애플", "MS", "어도비",
             "디즈니", "티빙", "웨이브"],
    "주거": ["관리비", "전기", "수도", "가스", "월세", "임대료", "한국전력", "도시가스"],
    "수입": ["급여", "이자", "환급", "월급", "보너스"],
}


# ── IMAP UTF-7 인코딩 (RFC 3501 modified UTF-7) ──────
def imap_utf7_encode(s: str) -> str:
    """폴더명을 IMAP modified UTF-7로 인코딩"""
    result = []
    buf = []

    def flush():
        if buf:
            encoded = base64.b64encode("".join(buf).encode("utf-16-be")).rstrip(b"=").decode("ascii")
            result.append("&" + encoded.replace("/", ",") + "-")
            buf.clear()

    for ch in s:
        code = ord(ch)
        if 0x20 <= code <= 0x7E and ch != "&":
            flush()
            result.append(ch)
        elif ch == "&":
            flush()
            result.append("&-")
        else:
            buf.append(ch)
    flush()
    return "".join(result)


# ── 헤더/본문 파싱 ────────────────────────────────────
def decode_str(s):
    if s is None:
        return ""
    parts = decode_header(s)
    out = ""
    for part, charset in parts:
        if isinstance(part, bytes):
            cs = charset or "utf-8"
            try:
                out += part.decode(cs, errors="replace")
            except (LookupError, UnicodeDecodeError):
                out += part.decode("utf-8", errors="replace")
        else:
            out += part
    return out


def strip_html(html: str) -> str:
    """HTML 태그 제거 + 엔티티 디코딩 + 공백 정리"""
    # <script>, <style> 블록 통째로 제거
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    # <br>, </p>, </div> 등 블록 끊기를 줄바꿈으로
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|div|tr|li|td|h[1-6])>", "\n", html, flags=re.IGNORECASE)
    # 나머지 태그 제거
    html = re.sub(r"<[^>]+>", " ", html)
    # 엔티티 디코딩 (&nbsp; 등)
    html = html_module.unescape(html)
    # 공백/줄바꿈 정리
    html = re.sub(r"[ \t ]+", " ", html)
    html = re.sub(r"\n\s*\n+", "\n", html)
    return html.strip()


def get_email_body(msg) -> str:
    """text/plain을 우선, 없으면 text/html을 strip해서 반환"""
    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                continue
            if content_type == "text/plain":
                plain += text + "\n"
            elif content_type == "text/html":
                html += text + "\n"
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = (payload or b"").decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html = text
            else:
                plain = text
        except (LookupError, UnicodeDecodeError):
            pass

    if plain.strip():
        return plain
    return strip_html(html)


# ── 금액/유형/가맹점 파싱 ─────────────────────────────
def _is_blacklisted_context(text: str, pos: int, window: int = 12) -> bool:
    """매치 위치 주변에 잔액/한도 등이 있는지"""
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    snippet = text[start:end]
    return any(kw in snippet for kw in AMOUNT_BLACKLIST_CONTEXT)


def parse_amount(text: str) -> int | None:
    """본문에서 거래 금액 추출. 거래 키워드 근접 매치 우선"""
    # 1순위: 거래 키워드와 함께 등장하는 금액
    for m in AMOUNT_NEAR_KEYWORD.finditer(text):
        amount_str = m.group(2) or m.group(3)
        if not amount_str:
            continue
        if _is_blacklisted_context(text, m.start()):
            continue
        try:
            return int(amount_str.replace(",", ""))
        except ValueError:
            continue

    # 2순위: 단순 N원 매치 중 블랙리스트 컨텍스트 제외
    for m in AMOUNT_FALLBACK.finditer(text):
        if _is_blacklisted_context(text, m.start()):
            continue
        try:
            value = int(m.group(1).replace(",", ""))
            if value < 100:  # 100원 미만은 노이즈일 가능성 높음
                continue
            return value
        except ValueError:
            continue
    return None


def parse_transaction_type(text: str, source: str | None = None) -> str:
    if source in ("나이스정보통신", "토스페이먼츠", "헥토파이낸셜", "네이버페이"):
        if "취소" in text or "환불" in text:
            return "입금"
        return "출금"
    # 입금 키워드 우선 (환불/취소 포함)
    for kw in INCOME_KEYWORDS:
        if kw in text:
            return "입금"
    for kw in EXPENSE_KEYWORDS:
        if kw in text:
            return "출금"
    return "출금"


def parse_merchant(text: str, source: str) -> str:
    patterns = []
    if "나이스" in source:
        patterns += [
            r"([^\s]+(?:주식회사|㈜)?[^\s]+)(?:에서|에서의)\s*결제",
            r"님,\s*(.+?)에서\s*결제",
        ]
    if "토스" in source:
        patterns += [r"님,\s*(.+?)에서\s*결제한"]
    if "헥토" in source:
        patterns += [
            r"\(주\)([^\s]+)에서",
            r"님,\s*(.+?)에서",
        ]
    if "네이버" in source:
        patterns += [r"결제처[:\s]*([^\n\r]+)"]
    if "카카오" in source:
        patterns += [r"(?:가맹점|결제처|내역)[:\s]*([^\n\r]+)"]
    if "BC" in source:
        patterns += [r"(?:가맹점|사용처)[:\s]*([^\n\r]+)"]
    if "IBK" in source or "기업" in source:
        patterns += [r"(?:내용|적요)[:\s]*([^\n\r]+)"]
    if "현대" in source:
        patterns += [r"(?:가맹점|사용처|이용내역)[:\s]*([^\n\r]+)"]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            value = m.group(1).strip()
            value = re.sub(r"\s+", " ", value)
            return value[:50]
    return "알 수 없음"


def guess_category(merchant: str, tx_type: str) -> str:
    if tx_type == "입금":
        return "수입"
    text = (merchant or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                return category
    return "기타"


# ── 비거래 이메일 분류 ────────────────────────────────
def classify_non_transaction(sender: str, subject: str) -> str | None:
    """비거래 이메일을 카테고리 폴더로 분류. 매칭되는 폴더 이름 또는 None"""
    sender_lower = sender.lower()
    for category, folder, sender_kws, subject_kws in NON_TX_CATEGORIES:
        for kw in sender_kws:
            if kw.lower() in sender_lower:
                return folder
        for kw in subject_kws:
            if kw.lower() in subject.lower():
                return folder
    return None


# ── BC카드 월간 명세서 PDF 파싱 ────────────────────────
def is_statement_email(subject: str) -> bool:
    return "이용대금명세서" in subject or "이용대금 명세서" in subject


def get_pdf_attachment(msg) -> tuple:
    """첫 번째 PDF 첨부의 (파일명, 바이트) 반환"""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename_raw = part.get_filename()
        filename = decode_str(filename_raw) if filename_raw else ""
        ctype = part.get_content_type()
        if ctype == "application/pdf" or filename.lower().endswith(".pdf"):
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            if payload:
                return filename, payload
    return None, None


def _norm_header_cell(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _find_col_idx(header: list, candidates: list):
    for i, h in enumerate(header):
        for c in candidates:
            if c in h:
                return i
    return None


def normalize_statement_date(raw: str, statement_year: int, statement_month: int):
    """다양한 날짜 포맷 → YYYY-MM-DD. 명세서 발행 월보다 큰 월은 전년도로 가정."""
    if not raw:
        return None
    raw = str(raw).strip()
    m = re.match(r"(\d{4})[\.\-/]\s*(\d{1,2})[\.\-/]\s*(\d{1,2})", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(\d{1,2})[\.\-/]\s*(\d{1,2})", raw)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        year = statement_year - 1 if month > statement_month else statement_year
        return f"{year}-{month:02d}-{day:02d}"
    return None


def normalize_statement_amount(raw: str):
    if raw is None:
        return None
    s = str(raw).replace(",", "").replace("원", "").replace(" ", "")
    if not s:
        return None
    sign = 1
    if s.startswith("-"):
        sign = -1
        s = s[1:]
    elif s.startswith("(") and s.endswith(")"):
        sign = -1
        s = s[1:-1]
    try:
        return int(s) * sign
    except ValueError:
        return None


def parse_statement_table(table: list, statement_year: int, statement_month: int) -> list:
    """pdfplumber.extract_tables()의 한 표를 거래 리스트로 변환"""
    if not table or len(table) < 2:
        return []
    header = [_norm_header_cell(c) for c in (table[0] or [])]
    date_idx = _find_col_idx(header, ["이용일", "사용일", "거래일", "승인일", "매출일"])
    merchant_idx = _find_col_idx(header, ["가맹점", "이용처", "사용처", "이용내역", "내용"])
    amount_idx = _find_col_idx(header, ["이용금액", "결제금액", "청구금액", "승인금액", "금액"])
    if date_idx is None or amount_idx is None:
        return []
    out = []
    for row in table[1:]:
        if not row:
            continue
        try:
            date_raw = row[date_idx]
            amount_raw = row[amount_idx]
            merchant = (row[merchant_idx] if merchant_idx is not None and merchant_idx < len(row) else "") or ""
        except IndexError:
            continue
        date = normalize_statement_date(date_raw or "", statement_year, statement_month)
        amount = normalize_statement_amount(amount_raw or "")
        if not date or not amount:
            continue
        merchant_clean = re.sub(r"\s+", " ", str(merchant)).strip()[:50] or "알 수 없음"
        out.append({
            "날짜": date,
            "시간": "",
            "출처": "BC카드",
            "유형": "입금" if amount < 0 else "출금",
            "금액": abs(amount),
            "내역": merchant_clean,
            "카테고리": guess_category(merchant_clean, "출금" if amount > 0 else "입금"),
            "원문": "BC카드 월간명세서",
        })
    return out


# 텍스트 한 줄에서 "MM/DD ... 가맹점 ... 12,345" 패턴 추출
PDF_TEXT_TX_LINE = re.compile(
    r"^\s*(?P<m>\d{1,2})[\.\-/](?P<d>\d{1,2})\s+(?P<merchant>.+?)\s+(?P<amount>-?[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})\s*$"
)


def parse_statement_text(text: str, statement_year: int, statement_month: int) -> list:
    """표 추출 실패 시 텍스트 라인 단위 fallback"""
    if not text:
        return []
    out = []
    for line in text.splitlines():
        m = PDF_TEXT_TX_LINE.match(line)
        if not m:
            continue
        month = int(m.group("m"))
        day = int(m.group("d"))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        year = statement_year - 1 if month > statement_month else statement_year
        date = f"{year}-{month:02d}-{day:02d}"
        amount = normalize_statement_amount(m.group("amount"))
        if not amount:
            continue
        merchant = re.sub(r"\s+", " ", m.group("merchant")).strip()[:50]
        if not merchant:
            continue
        out.append({
            "날짜": date,
            "시간": "",
            "출처": "BC카드",
            "유형": "입금" if amount < 0 else "출금",
            "금액": abs(amount),
            "내역": merchant,
            "카테고리": guess_category(merchant, "출금" if amount > 0 else "입금"),
            "원문": "BC카드 월간명세서",
        })
    return out


def parse_pdf_transactions(pdf_bytes: bytes, password: str,
                           statement_year: int, statement_month: int) -> list:
    """비밀번호 PDF에서 거래 추출 (표 우선, 텍스트 fallback)"""
    import io
    try:
        import pdfplumber
    except ImportError:
        print("⚠️  pdfplumber 미설치: PDF 파싱 건너뜀")
        return []

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes), password=password)
    except Exception as exc:
        print(f"PDF 열기 실패 (비밀번호 또는 포맷): {exc}")
        return []

    all_txs = []
    try:
        for page in pdf.pages:
            tables = []
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            page_had_table = False
            for table in tables:
                page_txs = parse_statement_table(table, statement_year, statement_month)
                if page_txs:
                    page_had_table = True
                    all_txs.extend(page_txs)
            if not page_had_table:
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                all_txs.extend(parse_statement_text(text, statement_year, statement_month))
    finally:
        try:
            pdf.close()
        except Exception:
            pass

    # 같은 (날짜, 내역, 금액) 중복 제거
    seen = set()
    deduped = []
    for tx in all_txs:
        key = (tx["날짜"], tx["내역"], tx["금액"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tx)
    return deduped


# ── IMAP 작업 ─────────────────────────────────────────
def connect_imap():
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(NAVER_EMAIL, NAVER_APP_PW)
    return mail


def ensure_folder(mail, folder_name: str) -> str:
    """폴더가 없으면 생성하고 IMAP UTF-7 인코딩된 이름 반환"""
    encoded = imap_utf7_encode(folder_name)
    try:
        mail.create(f'"{encoded}"')
    except Exception:
        pass
    return encoded


def move_email(mail, eid, dest_encoded: str, mark_seen: bool = True) -> bool:
    """이메일을 dest_encoded 폴더로 복사 후 원본 삭제 표시. EXPUNGE는 호출자 책임"""
    try:
        if mark_seen:
            mail.store(eid, "+FLAGS", "\\Seen")
        result, _ = mail.copy(eid, f'"{dest_encoded}"')
        if result != "OK":
            return False
        mail.store(eid, "+FLAGS", "\\Deleted")
        return True
    except Exception as exc:
        print(f"이동 실패 (eid={eid}, dest={dest_encoded}): {exc}")
        return False


def fetch_recent_ids(mail, folder: str, hours: int) -> list:
    """폴더에서 최근 N시간 이내 이메일 ID 리스트"""
    since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
    status, _ = mail.select(f'"{folder}"', readonly=False)
    if status != "OK":
        return []
    _, data = mail.search(None, f'(SINCE "{since}")')
    return data[0].split() if data and data[0] else []


def process_folder(mail, folder: str, hours: int, dest_folders: dict) -> tuple:
    """한 폴더를 처리: 거래 추출 + 비거래 분류 이동.
    반환: (transactions, moved_counts dict)"""
    transactions = []
    moved = {"거래": 0, "쇼핑": 0, "SNS": 0, "뉴스레터": 0, "광고": 0}

    eids = fetch_recent_ids(mail, folder, hours)
    if not eids:
        return transactions, moved

    for eid in eids:
        try:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender = decode_str(msg.get("From", ""))
            subject = decode_str(msg.get("Subject", ""))
            date_str = msg.get("Date", "")
        except Exception as exc:
            print(f"FETCH 실패 (folder={folder}, eid={eid}): {exc}")
            continue

        # 거래 이메일 매칭
        source = None
        for name, patterns in SENDER_PATTERNS.items():
            if any(p.lower() in sender.lower() for p in patterns):
                source = name
                break

        # BC카드 월간 명세서는 본문에 금액이 없으므로 별도 패스에서 처리
        if source == "BC카드" and is_statement_email(subject):
            continue

        if source:
            try:
                body = get_email_body(msg)
                full_text = subject + "\n" + body

                amount = parse_amount(full_text)
                if amount is None:
                    print(f"  · 금액 파싱 실패: [{source}] {subject[:50]}")
                    continue

                tx_type = parse_transaction_type(full_text, source)
                merchant = parse_merchant(full_text, source)
                category = guess_category(merchant, tx_type)

                try:
                    dt = parsedate_to_datetime(date_str)
                    tx_date = dt.strftime("%Y-%m-%d")
                    tx_time = dt.strftime("%H:%M")
                except Exception:
                    tx_date = datetime.now().strftime("%Y-%m-%d")
                    tx_time = ""

                transactions.append({
                    "날짜": tx_date,
                    "시간": tx_time,
                    "출처": source,
                    "유형": tx_type,
                    "금액": amount,
                    "내역": merchant,
                    "카테고리": category,
                    "원문": subject[:100],
                })

                if ENABLE_EMAIL_CLEANUP and "처리완료" in dest_folders:
                    if move_email(mail, eid, dest_folders["처리완료"]):
                        moved["거래"] += 1
            except Exception as exc:
                print(f"거래 파싱 실패 (eid={eid}): {exc}")
            continue

        # 비거래 이메일 분류
        if not ENABLE_EMAIL_CLEANUP:
            continue
        category_folder = classify_non_transaction(sender, subject)
        if not category_folder:
            continue
        encoded = dest_folders.get(category_folder)
        if not encoded:
            continue
        if move_email(mail, eid, encoded):
            # 카운트 키 매핑
            for cat_name, folder_name, _, _ in NON_TX_CATEGORIES:
                if folder_name == category_folder:
                    moved[cat_name] = moved.get(cat_name, 0) + 1
                    break

    # EXPUNGE는 select가 풀린 후엔 무효 — 폴더별로 마지막에 호출
    if ENABLE_EMAIL_CLEANUP:
        try:
            mail.expunge()
        except Exception as exc:
            print(f"EXPUNGE 실패 ({folder}): {exc}")

    return transactions, moved


def prepare_dest_folders(mail) -> dict:
    """이동 대상 폴더들을 미리 생성하고 인코딩 매핑 반환"""
    if not ENABLE_EMAIL_CLEANUP:
        return {}
    mapping = {
        "처리완료": ensure_folder(mail, PROCESSED_FOLDER),
        AD_FOLDER: ensure_folder(mail, AD_FOLDER),
        SHOPPING_FOLDER: ensure_folder(mail, SHOPPING_FOLDER),
        NEWSLETTER_FOLDER: ensure_folder(mail, NEWSLETTER_FOLDER),
        SNS_FOLDER: ensure_folder(mail, SNS_FOLDER),
    }
    return mapping


def process_statements(mail, folders: list, dest_folders: dict) -> tuple:
    """BC카드 월간 명세서 PDF 처리. (transactions, moved_count) 반환."""
    transactions = []
    moved_count = 0
    if not BC_PDF_PASSWORD:
        return transactions, moved_count

    since = (datetime.now() - timedelta(days=STATEMENT_LOOKBACK_DAYS)).strftime("%d-%b-%Y")

    for folder in folders:
        try:
            status, _ = mail.select(f'"{folder}"', readonly=False)
            if status != "OK":
                continue
            _, data = mail.search(None, f'(SINCE "{since}" FROM "bccard.com")')
            eids = data[0].split() if data and data[0] else []
        except Exception as exc:
            print(f"명세서 검색 실패 ({folder}): {exc}")
            continue

        for eid in eids:
            try:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject = decode_str(msg.get("Subject", ""))
                date_str = msg.get("Date", "")
            except Exception as exc:
                print(f"명세서 FETCH 실패 (eid={eid}): {exc}")
                continue

            if not is_statement_email(subject):
                continue

            try:
                dt = parsedate_to_datetime(date_str)
                s_year, s_month = dt.year, dt.month
            except Exception:
                now = datetime.now()
                s_year, s_month = now.year, now.month

            fname, pdf_bytes = get_pdf_attachment(msg)
            if not pdf_bytes:
                print(f"  · 명세서 첨부 없음: {subject[:60]}")
                continue

            print(f"  · 명세서 PDF 파싱 중: {fname or '(이름 없음)'} (size={len(pdf_bytes)}B)")
            pdf_txs = parse_pdf_transactions(pdf_bytes, BC_PDF_PASSWORD, s_year, s_month)
            if pdf_txs:
                transactions.extend(pdf_txs)
                print(f"    → {len(pdf_txs)}건 추출 ({s_year}-{s_month:02d} 명세)")
                if ENABLE_EMAIL_CLEANUP and "처리완료" in dest_folders:
                    if move_email(mail, eid, dest_folders["처리완료"]):
                        moved_count += 1
            else:
                print(f"    → 추출 0건. PDF 포맷/비번 확인 필요")

        if ENABLE_EMAIL_CLEANUP:
            try:
                mail.expunge()
            except Exception as exc:
                print(f"명세서 EXPUNGE 실패 ({folder}): {exc}")

    return transactions, moved_count


# ── Google Sheets 저장 ────────────────────────────────
def save_to_sheets(transactions: list):
    import json
    import tempfile

    import gspread
    from google.oauth2.service_account import Credentials

    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(creds_dict, f)
        creds_path = f.name

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    gc = gspread.authorize(creds)
    os.unlink(creds_path)

    sheet = gc.open_by_key(GOOGLE_SHEET_ID)
    print(f"📊 Google Sheet: {sheet.title} → {sheet.url}")

    if not transactions:
        print("새로운 거래 없음")
        return

    try:
        ws = sheet.worksheet("거래내역")
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet("거래내역", rows=10000, cols=10)
        ws.append_row(["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])

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
            tx["날짜"], tx["시간"], tx["출처"], tx["유형"],
            tx["금액"], tx["내역"], tx["카테고리"], tx["원문"],
        ])

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"✅ {len(new_rows)}개 거래 저장 완료")
    else:
        print("중복 없음, 새 거래 없음")


# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 이메일 파싱 시작 (cleanup={'on' if ENABLE_EMAIL_CLEANUP else 'off'})...")
    mail = connect_imap()

    dest_folders = prepare_dest_folders(mail)

    all_transactions = []
    total_moved = {"거래": 0, "쇼핑": 0, "SNS": 0, "뉴스레터": 0, "광고": 0}

    for folder in IMAP_FOLDERS:
        try:
            transactions, moved = process_folder(mail, folder, LOOKBACK_HOURS, dest_folders)
            all_transactions.extend(transactions)
            for k, v in moved.items():
                total_moved[k] = total_moved.get(k, 0) + v
        except Exception as exc:
            print(f"폴더 처리 실패 ({folder}): {exc}")

    # BC카드 월간 명세서 PDF 별도 패스 (LOOKBACK_DAYS 윈도우)
    if BC_PDF_PASSWORD:
        try:
            stmt_txs, stmt_moved = process_statements(mail, IMAP_FOLDERS, dest_folders)
            all_transactions.extend(stmt_txs)
            total_moved["명세서"] = stmt_moved
        except Exception as exc:
            print(f"명세서 처리 실패: {exc}")
    else:
        print("ℹ️  BC_PDF_PASSWORD 미설정: 월간 명세서 PDF 파싱 건너뜀")

    print(f"파싱된 거래: {len(all_transactions)}개")
    if ENABLE_EMAIL_CLEANUP:
        moved_summary = ", ".join(f"{k} {v}" for k, v in total_moved.items() if v)
        print(f"이동된 메일: {moved_summary or '없음'}")

    try:
        mail.logout()
    except Exception:
        pass

    save_to_sheets(all_transactions)
    print("완료!")


if __name__ == "__main__":
    main()
