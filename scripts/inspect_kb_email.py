"""KB국민카드 이메일 inspection — 워크플로 dispatch로 1회 실행.

발신자 cyberman@kbmail.kbcard.com / kbcard.com 도메인 메일을 검색해서
From·Subject·Date·본문(텍스트화)·첨부 파일 정보를 stdout에 출력.
PDF 첨부가 있으면 KB_PDF_PASSWORD로 복호화 시도해 텍스트 일부도 같이 출력.

목적: KB 명세서 본문/PDF 파서 작성에 필요한 구조 정보 수집.
한 번 쓰고 버릴 일회용 도구 — 본문 파서 추가 후 워크플로 삭제 권장.
"""

import email
import imaplib
import io
import os
import sys
from datetime import datetime, timedelta
from email.header import decode_header

NAVER_EMAIL = os.environ.get("NAVER_EMAIL", "")
NAVER_APP_PW = os.environ.get("NAVER_APP_PW", "")
KB_PDF_PASSWORD = os.environ.get("KB_PDF_PASSWORD", "")

if not NAVER_EMAIL or not NAVER_APP_PW:
    print("ERROR: NAVER_EMAIL / NAVER_APP_PW 환경변수가 필요합니다", file=sys.stderr)
    sys.exit(1)


def decode_str(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                text = text.decode(enc or "utf-8", errors="replace")
            except LookupError:
                text = text.decode("utf-8", errors="replace")
        out.append(text or "")
    return "".join(out)


def body_text(msg) -> str:
    """text/plain 우선, 없으면 text/html을 본문으로 반환."""
    plain, html = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").lower()
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
    return plain or html


def try_extract_pdf_text(payload: bytes, password: str) -> str | None:
    """PDF payload를 (필요 시 복호화 후) 텍스트로 변환. 실패 시 None."""
    if not payload:
        return None
    try:
        import pymupdf  # fitz
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return "[pymupdf 미설치 — PDF 텍스트 추출 불가]"
    try:
        doc = pymupdf.open(stream=payload, filetype="pdf")
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                return f"[복호화 실패 — 비밀번호 불일치 또는 미입력]"
        text_parts = []
        for i, page in enumerate(doc):
            t = page.get_text()
            text_parts.append(f"--- page {i+1} ---\n{t}")
        return "\n".join(text_parts)
    except Exception as e:
        return f"[PDF 파싱 오류: {e}]"


def main():
    print(f"NAVER_EMAIL: {NAVER_EMAIL[:3]}***{NAVER_EMAIL[-10:] if len(NAVER_EMAIL)>10 else ''}")
    print(f"KB_PDF_PASSWORD: {'설정됨' if KB_PDF_PASSWORD else '미설정'}")
    print()

    mail = imaplib.IMAP4_SSL("imap.naver.com", 993)
    mail.login(NAVER_EMAIL, NAVER_APP_PW)

    days = 60
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

    folders = ["INBOX", "&zK2tbAC3rLDIHA-", "&yPy7OA-|&vDDBoQ-", "&v0RoVMS9-_&yPy7OO2KAA-"]
    found_count = 0

    for folder in folders:
        try:
            status, _ = mail.select(folder)
            if status != "OK":
                continue
        except Exception as e:
            print(f"[폴더 {folder} 선택 실패: {e}]")
            continue
        try:
            _, data = mail.search(None, f'(SINCE "{since}" FROM "kbcard")')
        except Exception as e:
            print(f"[검색 실패 {folder}: {e}]")
            continue
        ids = data[0].split() if data and data[0] else []
        if not ids:
            continue

        print(f"\n{'='*70}")
        print(f"📁 폴더: {folder} — {len(ids)}건 발견")
        print(f"{'='*70}")

        for uid in ids[-3:]:  # 최근 3건만
            try:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
            except Exception as e:
                print(f"[UID {uid.decode()} fetch 실패: {e}]")
                continue
            found_count += 1
            print(f"\n{'-'*70}")
            print(f"📧 UID {uid.decode()}")
            print(f"  From    : {decode_str(msg.get('From',''))}")
            print(f"  Subject : {decode_str(msg.get('Subject',''))}")
            print(f"  Date    : {msg.get('Date','')}")
            print(f"  Multipart: {msg.is_multipart()}")

            # 본문
            body = body_text(msg)
            if body:
                snippet = body.strip()[:2000]
                print(f"\n  📄 본문 ({len(body):,}자, 앞 2000자):")
                print(snippet)
                if len(body) > 2000:
                    print("  ... (truncated)")
            else:
                print("\n  📄 본문: (없음)")

            # 첨부
            attachments = []
            for part in msg.walk():
                disp = str(part.get("Content-Disposition") or "")
                if "attachment" in disp.lower() or part.get_filename():
                    fn = decode_str(part.get_filename() or "")
                    payload = part.get_payload(decode=True) or b""
                    attachments.append((fn, part.get_content_type(), payload))
            if attachments:
                print(f"\n  📎 첨부 {len(attachments)}건:")
                for fn, ctype, payload in attachments:
                    print(f"    - {fn} ({ctype}, {len(payload):,}B)")
                    if fn.lower().endswith(".pdf") or ctype == "application/pdf":
                        text = try_extract_pdf_text(payload, KB_PDF_PASSWORD)
                        if text:
                            print(f"      📜 PDF 텍스트 (앞 3000자):")
                            for ln in text[:3000].splitlines():
                                print(f"      {ln}")
                            if len(text) > 3000:
                                print(f"      ... (PDF 텍스트 {len(text):,}자 중 3000자만 표시)")
            else:
                print("\n  📎 첨부: 없음")

    if not found_count:
        print("\n⚠️ KB 발신자 메일을 찾지 못함 — 폴더 검색 결과 0건")
        print("   확인: 메일이 다른 폴더에 있거나, 60일 이상 된 메일")

    mail.logout()


if __name__ == "__main__":
    main()
