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
        env=env, cwd=REPO_ROOT, capture_output=True, text=True,
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
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
