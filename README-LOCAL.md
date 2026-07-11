# 💻 PC 로컬 모드 전환 가이드 (Windows)

클라우드(GitHub Actions + Google Sheets + Streamlit Cloud) 대신 **모든 것을 내 PC에서**:
데이터는 SQLite 파일 하나(`data/budget.db`), 대시보드는 로컬 Streamlit,
이메일 수집은 Windows 작업 스케줄러. 폰에서 볼 필요가 없다면 이쪽이 압도적으로 빠르고 자유롭습니다.

## 0. 준비물
- Python 3.11+ (`python --version`으로 확인, 없으면 python.org 또는 `winget install Python.Python.3.12`)
- git (이미 클론해봤다면 있음)
- (권장) Claude Code: `winget install Anthropic.ClaudeCode` — 저장소에서 `claude` 실행하면
  시트 덤프·패치 워크플로 없이 DB를 직접 읽고 고칠 수 있습니다.

## 1. 저장소 준비
```powershell
cd C:\Users\RT_COM34\budget   # 기존 클론 위치
git pull origin main
pip install -r requirements.txt
```

## 2. secrets.toml 작성
`.streamlit\secrets.toml.example`을 `.streamlit\secrets.toml`로 복사하고 채웁니다.
이관 단계에서는 **Google 자격증명이 아직 필요**합니다 (원본을 읽어야 하므로):

```toml
# 아직 STORAGE 줄은 켜지 마세요 — 이관 후에 켭니다.
NAVER_EMAIL = "you@naver.com"
NAVER_APP_PW = "네이버 앱 비밀번호"
BC_PDF_PASSWORD = "생년월일6자리"
KAKAO_XLSX_PASSWORD = "생년월일6자리"

GOOGLE_SHEET_ID = "..."          # 기존 값 (GitHub secrets와 동일)
[gcp_service_account]
...                               # 서비스 계정 JSON 내용 그대로
```

## 3. 데이터 이관 (1회, 비파괴 — 구글 시트는 읽기만 함)
```powershell
python scripts\migrate_to_sqlite.py
```
`거래내역`(약 1,000행)·`예산`·`보금자리론`이 `data\budget.db`로 복사됩니다.

## 4. 로컬 모드 켜기
`secrets.toml` 맨 위에 두 줄 추가:
```toml
STORAGE = "sqlite"
DB_PATH = "data/budget.db"
```

## 5. 대시보드 실행
```powershell
streamlit run app.py
```
브라우저가 `http://localhost:8501`로 열립니다. 통합 업로드 인박스·분석·예산 전부 동일하게 작동 —
이제 Google API 왕복이 없어 훨씬 빠릅니다.

## 6. 이메일 수집 자동화 (BC/KB 명세서 + 카뱅 내보내기)
수동 실행:
```powershell
python scripts\run_local_fetch.py        # 최근 26시간
python scripts\run_local_fetch.py 720    # 밀린 30일치 일회 수집
```
매일 09:00 자동 실행 등록 (관리자 PowerShell):
```powershell
schtasks /create /tn "가계부수집" /sc daily /st 09:00 `
  /tr "cmd /c cd /d C:\Users\RT_COM34\budget && python scripts\run_local_fetch.py"
```
PC가 꺼져 있던 날은 다음 실행이 26시간+35일(명세서) lookback으로 메웁니다.

## 7. 백업
가계부 전체 = `data\budget.db` 파일 하나. 주기적으로 복사하거나,
`DB_PATH`를 OneDrive/Google Drive 동기화 폴더로 지정하면 자동 백업됩니다.

## 8. 클라우드 정리 (로컬이 안정된 뒤)
- GitHub Actions cron 끄기: `fetch_emails.yml`의 `schedule` 블록 삭제 (또는 repo Settings → Actions disable)
- Streamlit Cloud 앱 삭제 (선택)
- Google Sheets는 백업본으로 그냥 둬도 무방

## 되돌리기
`secrets.toml`에서 `STORAGE` 줄을 지우면 즉시 Google Sheets 모드로 복귀합니다.
(로컬 모드에서 쌓인 변경분은 시트에 없으므로, 병행 기간에는 한쪽만 쓰세요.)
