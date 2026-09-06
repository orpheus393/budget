"""로컬 이메일 구조 inspect 러너 — GitHub Actions inspect_email 워크플로의 PC 대체.

.streamlit/secrets.toml에서 자격증명을 읽어 inspect_email.py를 실행한다.
새 카드사 명세서 파서를 만들기 전에 메일 구조(본문·첨부·임베드 JS)를 확인하는 용도.

    python scripts/run_local_inspect.py hyundaicard        # 최근 60일, 3통
    python scripts/run_local_inspect.py kbcard 40 2        # 40일, 2통

출력에 시크릿은 포함되지 않지만 카드 거래 내역이 찍히므로 공유 시 주의.
"""

import os
import subprocess
import sys
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    secrets_path = os.path.join(REPO_ROOT, ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_path):
        print(f"❌ {secrets_path} 없음 — secrets.toml.example 참고")
        sys.exit(1)
    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    env = os.environ.copy()
    for key in ("NAVER_EMAIL", "NAVER_APP_PW"):
        if secrets.get(key):
            env[key] = str(secrets[key])
    if not env.get("NAVER_EMAIL") or not env.get("NAVER_APP_PW"):
        print("❌ secrets.toml에 NAVER_EMAIL / NAVER_APP_PW 필요")
        sys.exit(1)

    env["EMAIL_INSPECT_FROM"] = sys.argv[1]
    env["EMAIL_INSPECT_DAYS"] = sys.argv[2] if len(sys.argv) > 2 else "60"
    env["EMAIL_INSPECT_MAX"] = sys.argv[3] if len(sys.argv) > 3 else "3"
    # 명세서 PDF는 카드사 공통으로 생년월일 6자리 (BC와 같은 값)
    if secrets.get("BC_PDF_PASSWORD"):
        env["EMAIL_INSPECT_PDF_PW"] = str(secrets["BC_PDF_PASSWORD"])

    print(f"🔍 inspect: from={env['EMAIL_INSPECT_FROM']} "
          f"days={env['EMAIL_INSPECT_DAYS']} max={env['EMAIL_INSPECT_MAX']}")
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "inspect_email.py")],
        env=env, cwd=REPO_ROOT,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
