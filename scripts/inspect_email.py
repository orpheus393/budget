"""범용 이메일 inspect — workflow_dispatch로 실행해 메일 구조를 로그에 출력.

발신자 키워드(FROM 필터)와 검색 일수를 입력으로 받아, 매칭되는 메일의
From·Subject·Date·본문(텍스트)·첨부·임베드된 JS 변수(list_*Json)를 출력한다.
PDF 첨부가 있으면 EMAIL_INSPECT_PDF_PW로 복호화 시도해 텍스트 일부도 출력.

용도: 새 카드사/은행 명세서 파서를 만들기 전에 메일 구조를 파악하는 도구.
시크릿(NAVER_APP_PW 등)은 절대 출력하지 않는다.
"""

import email
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta
from email.header import decode_header

NAVER_EMAIL = os.environ.get("NAVER_EMAIL", "")
NAVER_APP_PW = os.environ.get("NAVER_APP_PW", "")
# 검색 발신자 키워드 (예: "kbcard", "shinhan") — workflow input에서 전달
FROM_FILTER = os.environ.get("EMAIL_INSPECT_FROM", "").strip()
DAYS = int(os.environ.get("EMAIL_INSPECT_DAYS", "60"))
MAX_MAILS = int(os.environ.get("EMAIL_INSPECT_MAX", "3"))
PDF_PW = os.environ.get("EMAIL_INSPECT_PDF_PW", "")

if not NAVER_EMAIL or not NAVER_APP_PW:
    print("ERROR: NAVER_EMAIL / NAVER_APP_PW 환경변수가 필요합니다", file=sys.stderr)
    sys.exit(1)
if not FROM_FILTER:
    print("ERROR: EMAIL_INSPECT_FROM 입력이 필요합니다 (예: kbcard)", file=sys.stderr)
    sys.exit(1)

# 검색 대상 폴더 (email_parser.py의 IMAP_FOLDERS와 동일 + 명세서/처리완료)
FOLDERS = [
    "INBOX",
    "&zK2tbAC3rLDIHA-",
    "&yPy7OA-|&vDDBoQ-",
    "&rZIwum2Y-",          # 가계부_명세서 (있으면)
    "&v0RwwYB5-_&yPy7OO2KAA-",
]


def decode_str(s):
    if not s:
        return ""
    out = []
    for text, enc in decode_header(s):
        if isinstance(text, bytes):
            try:
                text = text.decode(enc or "utf-8", errors="replace")
            except LookupError:
                text = text.decode("utf-8", errors="replace")
        out.append(text or "")
    return "".join(out)


def header_str(value) -> str:
    """msg.get(...) 결과를 안전하게 str로. None/Header/str 모두 처리.

    email 라이브러리는 헤더에 비ASCII가 있으면 str이 아닌 Header 객체를
    반환할 수 있다. 그대로 .lower()를 호출하면 AttributeError로 크래시.
    (email_parser.py의 동명 헬퍼와 동일 — 두 스크립트 모두 IMAP 파싱.)
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def best_text(msg):
    """text/plain 우선, 없으면 text/html 원문 반환 (디코딩만, strip 안 함)."""
    plain, html = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = header_str(part.get("Content-Disposition")).lower()
        if "attachment" in disp:
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            cs = part.get_content_charset() or "utf-8"
            text = payload.decode(cs, errors="replace")
        except Exception:
            continue
        if ctype == "text/plain":
            plain += text + "\n"
        elif ctype == "text/html":
            html += text + "\n"
    return plain, html


def strip_tags(html: str) -> str:
    import html as html_mod
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html_mod.unescape(html)
    return re.sub(r"\s+", " ", html).strip()


def find_js_vars(html: str):
    """list_*Json 같은 임베드 JS 변수명과 길이를 찾아 반환 (KB 명세서 패턴)."""
    found = []
    for m in re.finditer(r"var\s+(\w*[Jj]son\w*|list_\w+)\s*=\s*(\[.*?\]|\{.*?\});", html, re.S):
        name, body = m.group(1), m.group(2)
        found.append((name, len(body), body[:600]))
    return found


def try_pdf(payload: bytes, pw: str):
    if not payload:
        return None
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return "[pymupdf 미설치]"
    try:
        doc = pymupdf.open(stream=payload, filetype="pdf")
        if doc.needs_pass and not (pw and doc.authenticate(pw)):
            return "[복호화 실패 — 비밀번호 불일치/미입력]"
        return "\n".join(f"--- p{i+1} ---\n{pg.get_text()}" for i, pg in enumerate(doc))
    except Exception as e:
        return f"[PDF 오류: {e}]"


def main():
    print(f"검색 발신자: '{FROM_FILTER}' / 최근 {DAYS}일 / 폴더당 최대 {MAX_MAILS}건")
    print(f"PDF 비밀번호: {'설정됨' if PDF_PW else '미설정'}")
    mail = imaplib.IMAP4_SSL("imap.naver.com", 993)
    mail.login(NAVER_EMAIL, NAVER_APP_PW)
    since = (datetime.now() - timedelta(days=DAYS)).strftime("%d-%b-%Y")
    total = 0

    for folder in FOLDERS:
        try:
            if mail.select(folder)[0] != "OK":
                continue
            _, data = mail.search(None, f'(SINCE "{since}" FROM "{FROM_FILTER}")')
        except Exception as e:
            print(f"[{folder} 검색 실패: {e}]")
            continue
        ids = data[0].split() if data and data[0] else []
        if not ids:
            continue
        print(f"\n{'='*72}\n📁 {folder} — {len(ids)}건\n{'='*72}")

        for uid in ids[-MAX_MAILS:]:
            try:
                _, md = mail.fetch(uid, "(RFC822)")
                msg = email.message_from_bytes(md[0][1])
            except Exception as e:
                print(f"[fetch 실패 {uid}: {e}]")
                continue
            total += 1
            print(f"\n{'-'*72}")
            print(f"From    : {decode_str(msg.get('From',''))}")
            print(f"Subject : {decode_str(msg.get('Subject',''))}")
            print(f"Date    : {msg.get('Date','')}")

            plain, html = best_text(msg)
            if plain.strip():
                print(f"\n📄 text/plain ({len(plain):,}자, 앞 1500):")
                print(plain.strip()[:1500])
            if html.strip():
                js = find_js_vars(html)
                if js:
                    print(f"\n🧩 임베드 JS 변수 {len(js)}개:")
                    for name, ln, preview in js:
                        print(f"  - {name} ({ln:,}자): {preview[:300]}")
                stripped = strip_tags(html)
                print(f"\n📄 text/html strip ({len(stripped):,}자, 앞 1500):")
                print(stripped[:1500])

            for part in msg.walk():
                fn = decode_str(part.get_filename() or "")
                if not fn and "attachment" not in header_str(part.get("Content-Disposition")).lower():
                    continue
                if not fn:
                    continue
                payload = part.get_payload(decode=True) or b""
                print(f"\n📎 첨부: {fn} ({part.get_content_type()}, {len(payload):,}B)")
                if fn.lower().endswith(".pdf") or part.get_content_type() == "application/pdf":
                    txt = try_pdf(payload, PDF_PW)
                    if txt:
                        print(f"  📜 PDF 텍스트 앞 2500:")
                        print("\n".join("  " + l for l in txt[:2500].splitlines()))

    if not total:
        print(f"\n⚠️ '{FROM_FILTER}' 발신 메일 {DAYS}일 내 0건")
    mail.logout()


if __name__ == "__main__":
    main()
