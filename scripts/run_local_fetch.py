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
    env["LOOKBACK_HOURS"] = sys.argv[1] if len(sys.argv) > 1 else "26"
    env["STATEMENT_LOOKBACK_DAYS"] = "35"

    if not env.get("NAVER_EMAIL") or not env.get("NAVER_APP_PW"):
        print("❌ secrets.toml에 NAVER_EMAIL / NAVER_APP_PW 필요")
        sys.exit(1)

    print(f"📬 로컬 수집 시작 (lookback {env['LOOKBACK_HOURS']}h → {env['DB_PATH']})")
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "email_parser.py")],
        env=env, cwd=REPO_ROOT,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
