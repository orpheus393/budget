# 💰 가계부 자동화

## 구조
```
GitHub Actions (매시간)
    → 네이버 이메일 파싱 (기업은행, BC카드, 카카오뱅크)
    → Google Sheets 저장
          ↓
Streamlit Cloud (대시보드)
    → Google Sheets 읽기
    → 대시보드 표시
```

## 설정 순서

### 1. Google Cloud 서비스 계정 생성
1. https://console.cloud.google.com 접속
2. 새 프로젝트 생성 (예: "budget-app")
3. API 및 서비스 → Google Sheets API 활성화
4. API 및 서비스 → Google Drive API 활성화
5. 서비스 계정 만들기 → JSON 키 다운로드

### 2. Google Sheets 생성
1. Google Sheets에서 새 스프레드시트 생성
2. 서비스 계정 이메일을 편집자로 공유
3. URL에서 Sheet ID 복사 (docs.google.com/spreadsheets/d/**ID**/edit)

### 3. GitHub Secrets 설정
repo → Settings → Secrets and variables → Actions:
- `NAVER_EMAIL`: 네이버 이메일 주소
- `NAVER_APP_PW`: 네이버 앱 비밀번호
- `GOOGLE_SHEET_ID`: 구글 시트 ID
- `GOOGLE_CREDS_JSON`: 서비스 계정 JSON 전체 내용

### 4. Streamlit Cloud 배포
1. https://streamlit.io/cloud 접속
2. GitHub 연결 → 이 repo 선택
3. Main file: `app.py`
4. Secrets 설정 (secrets.toml.example 참고)

### 5. 현대카드
- 현대카드 앱 → 이용내역 → Excel 다운로드
- 대시보드의 "현대카드 내역 업로드" 섹션에서 업로드

## 네이버 IMAP 설정
- 네이버 메일 → 환경설정 → POP3/IMAP 설정 → **IMAP 사용함**
- 네이버 보안설정 → 외부 앱 비밀번호 발급

## 이메일 자동 정리 (선택)
워크플로 env `ENABLE_EMAIL_CLEANUP=true`이면 파싱 실행 시 다음을 수행합니다.
- 거래 알림 → `가계부_처리완료` 폴더로 이동
- 비거래 메일 자동 분류 (읽음 표시 + 폴더 이동):
  - 쇼핑/배송 → `가계부_쇼핑`
  - SNS 알림 → `가계부_SNS`
  - 뉴스레터 → `가계부_뉴스레터`
  - 광고/프로모션 → `가계부_광고`

폴더는 자동 생성됩니다. 비활성화하려면 워크플로 yml에서 `ENABLE_EMAIL_CLEANUP`을 `"false"`로 바꾸세요.
