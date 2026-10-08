"""로컬 이메일 구조 inspect 러너 — GitHub Actions inspect_email 워크플로의 PC 대체.

.streamlit/secrets.toml에서 자격증명을 읽어 inspect_email.py를 실행한다.
새 카드사 명세서 파서를 만들기 전에 메일 구조(본문·첨부·임베드 JS)를 확인하는 용도.

    python scripts/run_local_inspect.py hyundaicard        # 최근 60일, 3통
    python scripts/run_local_inspect.py kbcard 40 2        # 40일, 2통
    python scripts/run_local_inspect.py hyundaicard --raw  # 내용까지 (내 PC에서만)

기본은 '형태만' 출력 — 가맹점·금액·카드번호 대신 자리수 토큰(가7, #6)만 남아
그대로 공유해도 안전하다 (파서 설계에는 이 정도면 충분). 결과는 화면과
inspect_<발신자>.txt 파일에 함께 기록된다.
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

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw = "--raw" in sys.argv
    env["EMAIL_INSPECT_FROM"] = args[0]
    env["EMAIL_INSPECT_DAYS"] = args[1] if len(args) > 1 else "60"
    env["EMAIL_INSPECT_MAX"] = args[2] if len(args) > 2 else "3"
    env["EMAIL_INSPECT_REDACT"] = "0" if raw else "1"
    # 명세서 PDF는 카드사 공통으로 생년월일 6자리 (BC와 같은 값)
    if secrets.get("BC_PDF_PASSWORD"):
        env["EMAIL_INSPECT_PDF_PW"] = str(secrets["BC_PDF_PASSWORD"])

    out_path = os.path.join(REPO_ROOT, f"inspect_{env['EMAIL_INSPECT_FROM']}.txt")
    print(f"🔍 inspect: from={env['EMAIL_INSPECT_FROM']} "
          f"days={env['EMAIL_INSPECT_DAYS']} max={env['EMAIL_INSPECT_MAX']} "
          f"mode={'원문(공유 금지)' if raw else '형태만(공유 가능)'}")
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "inspect_email.py")],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"💾 저장: {out_path}"
          + ("" if raw else " — 이 파일은 그대로 공유해도 안전합니다"))
    if "Authentication failed" in output:
        print("\n❗ 네이버 로그인 실패 — 앱 비밀번호가 만료·변경됐을 수 있습니다.\n"
              "   네이버 > 내정보 > 보안설정 > 애플리케이션 비밀번호에서 새로 발급한 뒤\n"
              "   .streamlit/secrets.toml의 NAVER_APP_PW를 교체하세요.\n"
              "   (같은 값을 쓰는 매일 수집도 함께 멈춰 있습니다)")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
