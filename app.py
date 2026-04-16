"""
app.py - 가계부 Streamlit 대시보드
"""

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date
import calendar

# ── 페이지 설정 ──────────────────────────────────────
st.set_page_config(
    page_title="가계부 대시보드",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS 커스텀 ────────────────────────────────────────
st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem 1.5rem;
        border-radius: 12px;
        color: white;
        text-align: center;
        margin: 0.3rem 0;
    }
    .metric-income {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
    }
    .metric-expense {
        background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
    }
    .metric-balance {
        background: linear-gradient(135deg, #4776E6 0%, #8E54E9 100%);
    }
    .metric-label { font-size: 0.85rem; opacity: 0.9; }
    .metric-value { font-size: 1.8rem; font-weight: 700; }
    [data-testid="stMetricValue"] { font-size: 1.5rem; }
</style>
""", unsafe_allow_html=True)


# ── Google Sheets 로드 ────────────────────────────────
@st.cache_data(ttl=300)  # 5분 캐시
def load_data():
    try:
        # Streamlit secrets에서 인증 정보 가져오기
        creds_dict = dict(st.secrets["gcp_service_account"])
        sheet_id = st.secrets["GOOGLE_SHEET_ID"]

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(sheet_id)
        ws = sheet.worksheet("거래내역")
        data = ws.get_all_records()
        df = pd.DataFrame(data)

        if df.empty:
            return pd.DataFrame(columns=["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])

        df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
        df["금액"] = pd.to_numeric(df["금액"], errors="coerce").fillna(0)
        df = df.dropna(subset=["날짜"])
        df = df.sort_values("날짜", ascending=False)
        return df

    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        return pd.DataFrame(columns=["날짜", "시간", "출처", "유형", "금액", "내역", "카테고리", "원문"])


# ── 사이드바 ──────────────────────────────────────────
st.sidebar.title("💰 가계부")
st.sidebar.markdown("---")

df_all = load_data()

if not df_all.empty:
    min_date = df_all["날짜"].min().date()
    max_date = df_all["날짜"].max().date()
else:
    min_date = max_date = date.today()

# 월 선택
now = datetime.now()
months = []
for i in range(12):
    m = now.month - i
    y = now.year
    while m <= 0:
        m += 12
        y -= 1
    months.append(f"{y}년 {m:02d}월")

selected_month = st.sidebar.selectbox("📅 월 선택", months, index=0)
year = int(selected_month[:4])
month = int(selected_month[6:8])

# 카테고리 필터
if not df_all.empty and "카테고리" in df_all.columns:
    all_cats = ["전체"] + sorted(df_all["카테고리"].unique().tolist())
else:
    all_cats = ["전체"]
selected_cat = st.sidebar.selectbox("🏷️ 카테고리", all_cats)

# 데이터 필터링
df = df_all.copy()
if not df.empty:
    df = df[(df["날짜"].dt.year == year) & (df["날짜"].dt.month == month)]
    if selected_cat != "전체":
        df = df[df["카테고리"] == selected_cat]

# 새로고침
if st.sidebar.button("🔄 데이터 새로고침"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("현대카드 내역은 수동 업로드 필요")

# ── 메인 화면 ─────────────────────────────────────────
st.title(f"💰 {selected_month} 가계부")

# 요약 지표
income = df[df["유형"] == "입금"]["금액"].sum() if not df.empty else 0
expense = df[df["유형"] == "출금"]["금액"].sum() if not df.empty else 0
balance = income - expense

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(f"""
    <div class="metric-card metric-income">
        <div class="metric-label">💚 총 수입</div>
        <div class="metric-value">{income:,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col2:
    st.markdown(f"""
    <div class="metric-card metric-expense">
        <div class="metric-label">❤️ 총 지출</div>
        <div class="metric-value">{expense:,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col3:
    color = "metric-income" if balance >= 0 else "metric-expense"
    st.markdown(f"""
    <div class="metric-card {color}">
        <div class="metric-label">💙 잔액</div>
        <div class="metric-value">{balance:+,.0f}원</div>
    </div>""", unsafe_allow_html=True)
with col4:
    tx_count = len(df) if not df.empty else 0
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">📊 거래 건수</div>
        <div class="metric-value">{tx_count}건</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── 차트 ──────────────────────────────────────────────
if not df.empty:
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("📊 카테고리별 지출")
        expense_df = df[df["유형"] == "출금"].groupby("카테고리")["금액"].sum().reset_index()
        if not expense_df.empty:
            fig_pie = px.pie(
                expense_df, values="금액", names="카테고리",
                color_discrete_sequence=px.colors.qualitative.Set3,
                hole=0.4,
            )
            fig_pie.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=320)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("지출 데이터 없음")

    with col_right:
        st.subheader("📈 일별 수입/지출")
        daily = df.groupby(["날짜", "유형"])["금액"].sum().reset_index()
        if not daily.empty:
            fig_bar = px.bar(
                daily, x="날짜", y="금액", color="유형",
                color_discrete_map={"입금": "#38ef7d", "출금": "#f45c43"},
                barmode="group",
            )
            fig_bar.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=320,
                                   legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("데이터 없음")

    # 출처별 지출
    st.subheader("🏦 출처별 지출")
    source_df = df[df["유형"] == "출금"].groupby("출처")["금액"].sum().reset_index()
    if not source_df.empty:
        fig_source = px.bar(
            source_df.sort_values("금액", ascending=True),
            x="금액", y="출처", orientation="h",
            color="금액",
            color_continuous_scale="Reds",
        )
        fig_source.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=200,
                                  coloraxis_showscale=False)
        st.plotly_chart(fig_source, use_container_width=True)

    # ── 거래 내역 테이블 ──────────────────────────────
    st.subheader("📋 거래 내역")

    display_df = df[["날짜", "출처", "유형", "금액", "내역", "카테고리"]].copy()
    display_df["날짜"] = display_df["날짜"].dt.strftime("%Y-%m-%d")
    display_df["금액"] = display_df["금액"].apply(lambda x: f"{x:,.0f}원")

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "유형": st.column_config.TextColumn(width="small"),
            "출처": st.column_config.TextColumn(width="small"),
            "카테고리": st.column_config.TextColumn(width="small"),
        }
    )

else:
    st.info(f"📭 {selected_month} 데이터가 없어요. 이메일 파싱이 실행되면 자동으로 채워집니다.")

# ── 현대카드 수동 업로드 ──────────────────────────────
st.markdown("---")
st.subheader("💳 현대카드 내역 업로드")
col_up, col_info = st.columns([1, 2])
with col_up:
    uploaded = st.file_uploader("현대카드 Excel/CSV 파일", type=["xlsx", "csv"])
    if uploaded:
        try:
            if uploaded.name.endswith(".csv"):
                card_df = pd.read_csv(uploaded, encoding="cp949")
            else:
                card_df = pd.read_excel(uploaded)
            st.success(f"✅ {len(card_df)}건 로드됨")
            st.dataframe(card_df.head(), use_container_width=True)
            st.info("현대카드 파일 컬럼 구조를 확인 후 파싱 로직을 추가할게요.")
        except Exception as e:
            st.error(f"파일 오류: {e}")
with col_info:
    st.markdown("""
    **현대카드 내역 내보내기 방법:**
    1. 현대카드 앱 → 이용내역
    2. 우측 상단 다운로드 아이콘
    3. Excel 파일 저장
    4. 여기에 업로드
    """)
