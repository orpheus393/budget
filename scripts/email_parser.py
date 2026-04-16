"""
email_parser.py
네이버 이메일에서 은행/카드 알림을 파싱해서 Google Sheets에 저장
"""

import imaplib
import email
import re
import os
from datetime import datetime, timedelta
from email.header import decode_header
import gspread
from google.oauth2.service_account import Credentials

# ── 설정 ──────────────────────────────────────────────
NAVER_EMAIL = os.environ.get("NAVER_EMAIL", "")
NAVER_APP_PW = os.environ.get("NAVER_APP_PW", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")  # JSON 문자열

IMAP_HOST = "imap.naver.com"
IMAP_PORT = 993

# 실제 발신자 이메일 주소 기반 매핑
SENDER_PATTERNS = {
    "BC카드": ["bcbill@bccard.com", "bccard.com"],
    "카카오뱅크": ["no-reply@mail.kakaobank.com", "kakaobank.com"],
    "현대카드": ["admin@hyundaicard.com", "hyundaicard.com"],
    "IBK기업은행": ["ibk.co.kr", "기업은행"],
    "네이버페이": ["naverpayadmin_noreply@navercorp.com"],
    "토스페이먼츠": ["bill@bill-mail.tosspayments.com", "tosspayments.com"],
    "나이스정보통신": ["nice_customer@nicepg.co.kr"],
    "헥토파이낸셜": ["noreply@hecto.co.kr"],
    "쿠팡": ["no_reply@coupang.com", "noreply@e.coupang.com"],
}

# 청구/결제 관련 IMAP 폴더 (받은편지함 + 스마트메일함)
IMAP_FOLDERS = [
    "INBOX",
    "&zK2tbAC3rLDIHA-",   # 청구/결제 폴더
    "&yPy7OA-|&vDDBoQ-",  # 네이버페이 등
]

# ── 금액 파싱 정규식 ───────────────────────────────────
AMOUNT_PATTERNS = [
    r'([0-9,]+)원\s*(?:출금|이체|결제|승인|사용)',
    r'(?:출금|이체|결제|승인|사용)\s*([0-9,]+)원',
    r'금액[:\s]*([0-9,]+)원',
    r'([0-9,]+)원',
]

INCOME_KEYWORDS = ["입금", "수신", "급여", "이자", "환급"]
EXPENSE_KEYWORDS = ["출금", "이체", "결제", "승인", "사용", "출금완료"]


def connect_imap():
    """네이버 IMAP 연결"""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(NAVER_EMAIL, NAVER_APP_PW)
    return mail


def decode_str(s):
    """이메일 헤더 디코딩"""
    if s is None:
        return ""
    decoded = decode_header(s)
    result = ""
    for part, charset in decoded:
        if isinstance(part, bytes):
            charset = charset or "utf-8"
            try:
                result += part.decode(charset, errors="replace")
            except Exception:
                result += part.decode("utf-8", errors="replace")
        else:
            result += part
    return result


def get_email_body(msg):
    """이메일 본문 추출"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type in ["text/plain", "text/html"]:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body += payload.decode(charset, errors="replace")
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
        except Exception:
            pass
    return body


def parse_amount(text):
    """본문에서 금액 추출"""
    for pattern in AMOUNT_PATTERNS:
        match = re.search(pattern, text)
        if match:
            amount_str = match.group(1).replace(",", "")
            try:
                return int(amount_str)
            except ValueError:
                continue
    return None


def parse_transaction_type(text):
    """입금/출금 구분"""
    for keyword in INCOME_KEYWORDS:
        if keyword in text:
            return "입금"
    for keyword in EXPENSE_KEYWORDS:
        if keyword in text:
            return "출금"
    return "기타"


def parse_merchant(text, source):
    """가맹점/내역 추출"""
    # 카카오뱅크
    if "카카오" in source:
        match = re.search(r'(?:가맹점|결제처|내역)[:\s]*([^\n\r]+)', text)
        if match:
            return match.group(1).strip()[:50]

    # BC카드
    if "BC" in source:
        match = re.search(r'(?:가맹점|사용처)[:\s]*([^\n\r]+)', text)
        if match:
            return match.group(1).strip()[:50]

    # 기업은행
    if "IBK" in source or "기업" in source:
        match = re.search(r'(?:내용|적요)[:\s]*([^\n\r]+)', text)
        if match:
            return match.group(1).strip()[:50]

    return "알 수 없음"


def guess_category(merchant, tx_type):
    """카테고리 자동 분류"""
    categories = {
        "식비": ["식당", "음식", "카페", "커피", "배달", "맥도날드", "스타벅스", "편의점", "GS25", "CU", "세븐"],
        "교통": ["택시", "버스", "지하철", "주유", "카카오택시", "티머니", "하이패스"],
        "쇼핑": ["쿠팡", "네이버", "G마켓", "옥션", "11번가", "이마트", "홈플러스", "코스트코"],
        "의료": ["병원", "약국", "의원", "클리닉"],
        "통신": ["SKT", "KT", "LG", "통신", "인터넷"],
        "구독": ["넷플릭스", "유튜브", "스포티파이", "왓챠", "애플"],
        "수입": ["급여", "이자", "환급"],
    }
    if tx_type == "입금":
        return "수입"
    text = merchant.lower()
    for category, keywords in categories.items():
        for kw in keywords:
            if kw.lower() in text:
                return category
    return "기타"


def fetch_new_emails(mail, hours=2):
    """여러 폴더에서 최근 N시간 이내 알림 이메일 가져오기"""
    all_emails = []  # (folder, email_id) 튜플 목록
    since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")

    for folder in IMAP_FOLDERS:
        try:
            status, _ = mail.select(f'"{folder}"', readonly=True)
            if status != 'OK':
                continue
            _, data = mail.search(None, f'(SINCE "{since}")')
            ids = data[0].split()
            for eid in ids:
                all_emails.append((folder, eid))
        except Exception as e:
            print(f"폴더 {folder} 오류: {e}")

    return all_emails


def process_emails(mail, email_tuples):
    """이메일 파싱 → 거래 목록 반환"""
    transactions = []
    seen = set()

    for folder, eid in email_tuples:
        try:
            mail.select(f'"{folder}"', readonly=True)
        except:
            continue
        for eid in [eid]:
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        sender = decode_str(msg.get("From", ""))
        subject = decode_str(msg.get("Subject", ""))
        date_str = msg.get("Date", "")

        # 발신자 확인 (은행/카드사만)
        source = None
        for name, patterns in SENDER_PATTERNS.items():
            if any(p in sender for p in patterns):
                source = name
                break
        if not source:
            continue

        body = get_email_body(msg)
        full_text = subject + " " + body

        amount = parse_amount(full_text)
        if not amount:
            continue

        tx_type = parse_transaction_type(full_text)
        merchant = parse_merchant(full_text, source)
        category = guess_category(merchant, tx_type)

        # 날짜 파싱
        try:
            from email.utils import parsedate_to_datetime
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

    return transactions


def save_to_sheets(transactions):
    """Google Sheets에 저장"""
    if not transactions:
        print("새로운 거래 없음")
        return

    import json
    import tempfile

    # 인증
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

    # 시트 선택 또는 생성
    try:
        ws = sheet.worksheet("거래내역")
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet("거래내역", rows=10000, cols=10)
        ws.append_row(["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])

    # 중복 체크 (기존 데이터 로드)
    existing = ws.get_all_values()
    existing_keys = set()
    for row in existing[1:]:  # 헤더 제외
        if len(row) >= 5:
            key = f"{row[0]}_{row[2]}_{row[4]}"  # 날짜_출처_금액
            existing_keys.add(key)

    # 새 거래만 추가
    new_rows = []
    for tx in transactions:
        key = f"{tx['날짜']}_{tx['출처']}_{tx['금액']}"
        if key not in existing_keys:
            new_rows.append([
                tx["날짜"], tx["시간"], tx["출처"], tx["유형"],
                tx["금액"], tx["내역"], tx["카테고리"], tx["원문"]
            ])

    if new_rows:
        ws.append_rows(new_rows)
        print(f"✅ {len(new_rows)}개 거래 저장 완료")
    else:
        print("중복 없음, 새 거래 없음")


def main():
    print(f"[{datetime.now()}] 이메일 파싱 시작...")
    mail = connect_imap()
    email_tuples = fetch_new_emails(mail, hours=2)
    print(f"검색된 이메일: {len(email_tuples)}개 (전체 폴더 합계)")
    transactions = process_emails(mail, email_tuples)
    print(f"파싱된 거래: {len(transactions)}개")
    mail.logout()
    save_to_sheets(transactions)
    print("완료!")


if __name__ == "__main__":
    main()
