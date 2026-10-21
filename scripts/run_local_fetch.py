"""로컬 이메일 수집 러너 — GitHub Actions cron의 PC 대체.

.streamlit/secrets.toml에서 자격증명을 읽어 환경변수로 넘기고
email_parser.py를 실행한다. 카드 명세서·카뱅 내보내기 메일을
로컬 SQLite(data/budget.db)에 저장 (STORAGE=sqlite 자동 설정).

    python scripts/run_local_fetch.py            # 기본 26시간 lookback
    python scripts/run_local_fetch.py 720        # 30일치 일회 수집

Windows 작업 스케줄러 등록 (매일 09:00, PC 켜져 있을 때):
    schtasks /create /tn "가계부수집" /sc daily /st 09:00 ^
      /tr "\"C:\\...\\python.exe\" \"C:\\...\\budget\\scripts\\run_local_fetch.py\""
"""

import os
import subprocess
import sys
import tomllib
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    secrets_path = os.path.join(REPO_ROOT, ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_path):
        print(f"❌ {secrets_path} 없음 — secrets.toml.example 참고")
        sys.exit(1)
    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    env = os.environ.copy()
    # 시크릿 → 환경변수 (GitHub Actions secrets와 동일한 키)
    for key in ("NAVER_EMAIL", "NAVER_APP_PW", "BC_PDF_PASSWORD",
                "KAKAO_XLSX_PASSWORD", "GOOGLE_SHEET_ID"):
        if secrets.get(key):
            env[key] = str(secrets[key])
    if secrets.get("gcp_service_account"):
        import json
        env["GOOGLE_CREDS_JSON"] = json.dumps(dict(secrets["gcp_service_account"]))

    # 로컬 모드 강제: SQLite에 저장
    env["STORAGE"] = "sqlite"
    env["DB_PATH"] = str(secrets.get(
        "DB_PATH", os.path.join(REPO_ROOT, "data", "budget.db")))
    env["ENABLE_EMAIL_CLEANUP"] = str(secrets.get("ENABLE_EMAIL_CLEANUP", "true"))
    env["EMAIL_SUMMARY"] = str(secrets.get("EMAIL_SUMMARY", "true"))
    env["LOOKBACK_HOURS"] = sys.argv[1] if len(sys.argv) > 1 else "26"
    env["STATEMENT_LOOKBACK_DAYS"] = "35"

    if not env.get("NAVER_EMAIL") or not env.get("NAVER_APP_PW"):
        print("❌ secrets.toml에 NAVER_EMAIL / NAVER_APP_PW 필요")
        sys.exit(1)

    print(f"📬 로컬 수집 시작 (lookback {env['LOOKBACK_HOURS']}h → {env['DB_PATH']})")
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "email_parser.py")],
        env={**env, "PYTHONIOENCODING": "utf-8"}, cwd=REPO_ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output, end="")

    # 작업 스케줄러로 돌면 화면 출력이 사라진다. 실패를 나중에 추적할 수 있게
    # 항상 로그로 남긴다 (조용한 실패 방지).
    log_dir = os.path.join(REPO_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"fetch_{datetime.now():%Y%m%d_%H%M%S}.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"📝 로그: {log_path}")

    if "Authentication failed" in output:
        print("\n❗ 네이버 로그인 실패 — 앱 비밀번호가 만료·변경된 것으로 보입니다.\n"
              "   네이버 > 내정보 > 보안설정 > 애플리케이션 비밀번호에서 새로 발급한 뒤\n"
              "   .streamlit/secrets.toml의 NAVER_APP_PW를 교체하세요.\n"
              "   교체 전까지 매일 수집은 아무것도 가져오지 못합니다.")

    if result.returncode != 0:
        notify_failure(output, log_path, env)
    sys.exit(result.returncode)


# ── 실패 알림 ──────────────────────────────────────────
# 작업 스케줄러로 돌면 실패해도 아무도 모른다(9/19 로그인 실패 18일, 10/20 cp949
# 크래시 2일을 사람이 직접 들여다보고서야 알았다). 요약 메일은 성공 시에만 나간다.
# 두 경로로 알린다:
#   1) Windows 화면 알림 — 비밀번호 불필요, 네이버 로그인이 죽어도 뜬다.
#      이 작업은 로그인 세션(Interactive)에서 돌기 때문에 화면에 보인다.
#   2) 네이버 메일 — 폰에서도 보인다. 단 원인이 로그인 실패면 같은 앱 비밀번호를
#      써서 함께 실패하므로, 이 경우는 1)이 유일한 알림이다.
# 어느 쪽이 실패해도 수집 결과(종료 코드)는 바꾸지 않는다.

def _failure_reason(output: str) -> str:
    """로그에서 사람이 읽을 원인 한 줄."""
    if "Authentication failed" in output:
        return "네이버 로그인 실패 — 앱 비밀번호를 새로 발급해 secrets.toml을 교체하세요"
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    for l in reversed(lines):
        if "Error" in l or "Exception" in l:
            return l[:200]
    return lines[-1][:200] if lines else "출력 없음"


def _toast(title: str, body: str) -> bool:
    """Windows 화면 알림. 한글·따옴표 문제를 피하려고 -EncodedCommand로 넘긴다."""
    import base64

    def esc(s: str) -> str:
        return s.replace("'", "''")

    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x = $t.GetElementsByTagName('text');"
        f"$x.Item(0).AppendChild($t.CreateTextNode('{esc(title)}')) > $null;"
        f"$x.Item(1).AppendChild($t.CreateTextNode('{esc(body)}')) > $null;"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app)"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _mail(subject: str, body: str, env: dict) -> bool:
    import smtplib
    from email.mime.text import MIMEText

    user, pw = env.get("NAVER_EMAIL", ""), env.get("NAVER_APP_PW", "")
    if not user or not pw:
        return False
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, user
    try:
        with smtplib.SMTP("smtp.naver.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, pw)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def notify_failure(output: str, log_path: str, env: dict) -> None:
    reason = _failure_reason(output)
    shown = _toast("가계부 수집 실패", reason)
    sent = _mail(f"❗ [가계부] 수집 실패 {datetime.now():%m-%d %H:%M}",
                 f"오늘 가계부 자동 수집이 실패했습니다.\n\n원인: {reason}\n\n"
                 f"로그 파일: {log_path}\n\n"
                 "거래 메일은 저장이 끝나야 옮겨지므로, 원인을 고치면 다음 수집이 "
                 "빠진 분을 다시 읽습니다.", env)
    print(f"🔔 실패 알림 — 화면: {'표시' if shown else '실패'}, 메일: {'발송' if sent else '실패'}")
    if not (shown or sent):
        print("⚠️ 알림 두 경로 모두 실패. 로그 파일을 직접 확인하세요.")


if __name__ == "__main__":
    main()
