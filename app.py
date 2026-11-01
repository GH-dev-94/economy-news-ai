"""경제 뉴스 AI 자동화 대시보드 (Micro-SaaS)
5단계 파이프라인:
  [Step 1] 전날 경제 이슈 정리
  [Step 2] 검색량 상위 경제 키워드 N개 추출
  [Step 3] 키워드별 최신 뉴스 및 자료 수집
  [Step 4] 대중 관심사 및 핵심 쟁점 AI 분석
  [Step 5] 이슈 요약 및 셀프 Q&A 3개 원고 자동 생성
"""
import time
import os
import sys
from datetime import datetime, timedelta
from streamlit.runtime import exists
import streamlit as st
from dotenv import load_dotenv
from pipeline import (extract_keywords, fetch_news, generate_manuscripts_batch, build_newsletter_md, fetch_search_queries, period_text,
                      MAX_SPAN_DAYS, MAX_LOOKBACK_DAYS)

# =============================================================================
# VS Code '▶(실행)' 버튼 또는 'python app.py' 실행 시 자동 streamlit run 전환
# =============================================================================
if __name__ == "__main__" and not exists():
    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", sys.argv[0]]
    sys.exit(stcli.main())

# 환경변수 로드 (.env)
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH, override=True)  # 실행 위치와 무관하게 app.py 옆의 .env를 항상 먼저 읽음

# =============================================================================
# 페이지 설정 & UI 스타일링
# =============================================================================
st.set_page_config(
    page_title="경제 뉴스 AI 에이전트",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 커스텀 CSS (세련된 카드 및 헤더 디자인)
st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.05rem;
        color: #888888;
        margin-bottom: 1.5rem;
    }
    .pipeline-badge {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        font-size: 0.8rem;
        font-weight: 700;
        border-radius: 4px;
        background-color: #2b303c;
        color: #3b82f6;
        margin-bottom: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">📈 경제 뉴스 AI 자동화 대시보드</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">주요 경제 이슈 수집부터 핵심 키워드 도출, 대중 관심사 Q&A 뉴스레터 발행까지 원클릭 자동화</div>', unsafe_allow_html=True)

# 날짜 표시 및 검색 기간 선택 (기본값: 오늘)
_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
today = datetime.now()
fmt = lambda d: f"{d.year}년 {d.month}월 {d.day}일 ({_WEEKDAYS[d.weekday()]})"

# 빠른 선택 버튼 (콜백으로 달력 값 변경) - 기본값은 오늘
if "date_range" not in st.session_state:
    st.session_state["date_range"] = (today.date(), today.date())


def _set_range(days_back: int, end_back: int = 0):
    end = today.date() - timedelta(days=end_back)
    st.session_state["date_range"] = (today.date() - timedelta(days=days_back), end)


d1, d2 = st.columns([1, 2])
d1.metric("📅 오늘 날짜", fmt(today))
with d2:
    q1, q2, q3 = st.columns(3)
    q1.button("오늘", on_click=_set_range, args=(0,), use_container_width=True)
    q2.button("어제", on_click=_set_range, args=(1, 1), use_container_width=True)
    q3.button("지난 일주일", on_click=_set_range, args=(6,), use_container_width=True,
              help="오늘 포함 최근 7일 (오늘 ~ 6일 전)")
    picked = st.date_input(
        "🔎 검색 대상일 / 기간 (하루만 선택하면 그날 하루, 두 날짜를 선택하면 기간)",
        key="date_range",
        min_value=today.date() - timedelta(days=MAX_LOOKBACK_DAYS),
        max_value=today.date(),
        help=f"최대 {MAX_LOOKBACK_DAYS}일 전까지 선택 가능, 기간은 최대 {MAX_SPAN_DAYS}일. "
             "선택한 기간 동안의 주요 분야별 경제 헤드라인을 수집하여 핵심 키워드를 선별합니다.",
    )
if isinstance(picked, (tuple, list)):
    if len(picked) == 0:
        st.info("검색 날짜를 선택하세요.")
        st.stop()
    start_d, end_d = picked[0], picked[-1]
    if len(picked) == 1:
        st.info("기간의 끝 날짜를 선택하세요. (하루만 검색하려면 같은 날짜를 한 번 더 클릭)")
        st.stop()
else:
    start_d = end_d = picked
if (end_d - start_d).days + 1 > MAX_SPAN_DAYS:
    st.error(f"기간은 최대 {MAX_SPAN_DAYS}일까지 선택할 수 있습니다. 현재 {(end_d - start_d).days + 1}일 선택됨.")
    st.stop()
start_dt = datetime(start_d.year, start_d.month, start_d.day)
end_dt = datetime(end_d.year, end_d.month, end_d.day)
if start_d == end_d:
    period_label = fmt(start_dt)
else:
    period_label = f"{fmt(start_dt)} ~ {fmt(end_dt)} ({(end_d - start_d).days + 1}일간)"
st.caption(f"※ **{period_label}** 기준으로 주요 경제 뉴스와 핵심 이슈를 분석합니다.")

# =============================================================================
# 사이드바 설정 영역
# =============================================================================
with st.sidebar:
    st.header("⚙️ 파이프라인 설정")
    
    # API 키 상태 표시 (1순위: .env, 2순위: Streamlit Cloud Secrets, 3순위: 직접 입력)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if not gemini_key:
        try:
            gemini_key = str(st.secrets.get("GEMINI_API_KEY", "")).strip().strip('"').strip("'")
        except Exception:
            pass

    if gemini_key:
        st.success("✔ GEMINI_API_KEY 로드됨")
    else:
        st.info("ℹ️ GEMINI_API_KEY가 없습니다. 아래에 입력하거나 클라우드 Secrets에 등록하세요.")
        user_key = st.text_input("Gemini API Key (선택)", type="password", placeholder="AI Studio 키 입력")
        if user_key:
            gemini_key = user_key

    st.markdown("---")
    top_n = st.slider("🎯 추출 키워드 개수 (N개)", min_value=1, max_value=5, value=3, help="발행할 상위 경제 이슈 키워드 수")
    target_country = st.selectbox("🌐 대상 국가", ["대한민국 (KR)", "미국 (US)"], index=0)
    
    st.markdown("---")
    st.caption("🤖 Powered by Streamlit & Google Gemini")

# =============================================================================
# 메인 작업 영역 (5단계 자동화 파이프라인)
# =============================================================================
col1, col2 = st.columns([1, 4])
with col1:
    start_btn = st.button("🚀 뉴스 자동 생성 시작", type="primary", use_container_width=True)

if start_btn:
    country = "KR" if "KR" in target_country else "US"

    if not gemini_key:
        st.error("Gemini API Key가 필요합니다. `.env`의 GEMINI_API_KEY 또는 사이드바에 입력하세요. "
                 "(키 발급: https://aistudio.google.com/apikey)")
        st.stop()

    results = {}
    queries = {}
    with st.status("🤖 AI 뉴스 파이프라인 에이전트 가동 중...", expanded=True) as status:
        # [Step 1~2] 전날 이슈 정리 + 트렌드 키워드 추출
        st.write(f"📊🔍 **[1~2단계] {period_label} 분야별 경제 헤드라인 수집 및 핵심 키워드 {top_n}개 선별 중...**")
        keywords, info = extract_keywords(top_n, country, start_dt, end_dt, gemini_key)
        st.caption(f"경제 헤드라인 {info['headlines_count']}건 수집·분석 → {info['method']}")
        _reasons = {"quota": "Gemini 무료 한도(429) 초과", "error": "Gemini 호출 오류", "nokey": "API 키 없음", "nodata": "수집 데이터 부족"}
        if info.get("mode") == "gemini":
            st.success("🟢 키워드 선별: **Gemini AI (원래 로직)**" + (" · 30분 캐시 사용" if info.get("cached") else ""))
        else:
            _why = _reasons.get(info.get("fallback_reason"), "알 수 없음")
            st.warning(f"🟠 키워드 선별: **규칙 기반 대체 로직** — 사유: {_why}")
            if "gemini_error" in info:
                st.caption(f"상세: {' '.join(info['gemini_error'].split())[:200]}")
        if not keywords:
            status.update(label="키워드를 추출하지 못했습니다", state="error")
            st.stop()
        st.caption(f"추출된 키워드: {', '.join(keywords)}")

        # [Step 3] 자료 수집
        news = {}
        for kw in keywords:
            st.write(f"📰 **[3단계] '{kw}' 최신 뉴스 수집 중...**")
            news[kw] = fetch_news(kw, country, start_dt, end_dt)
            st.caption(f"기사 {len(news[kw])}건")

        # [Step 4~5] AI 분석 + 원고 생성 (모든 키워드를 Gemini 1회 호출로 처리)
        for kw in keywords:
            queries[kw] = fetch_search_queries(kw, country)
            st.caption(f"'{kw}' 실제 검색 연관어 {len(queries[kw])}건: " + ", ".join(queries[kw][:6]))
        st.write(f"🧠 **[4~5단계] Gemini 1회 호출로 {len(keywords)}개 키워드 뉴스레터 작성 중...**")
        try:
            results = generate_manuscripts_batch(
                [{"keyword": kw, "articles": news[kw], "queries": queries[kw]} for kw in keywords],
                start_dt, end_dt, gemini_key)
            st.success("🟢 뉴스레터 생성: **Gemini AI** 완료")
        except Exception as e:
            results = {kw: {"error": str(e)} for kw in keywords}
            _msg = "한도(429) 초과" if "429" in str(e) else "오류"
            st.error(f"🔴 뉴스레터 생성 실패 (Gemini {_msg}) — 아래 상세 참고")


        status.update(label="🎉 5단계 자동화 파이프라인 완료!", state="complete", expanded=False)

    st.markdown("---")
    st.subheader("📰 오늘의 경제 뉴스레터")
    st.caption(f"검색 기간: {period_label} · 발행일: {fmt(today)}")

    items = [(kw, results[kw], news[kw]) for kw in keywords]
    newsletter_md = build_newsletter_md(items, start_dt, end_dt, today)
    st.download_button("📥 뉴스레터 전체 다운로드 (.md)", newsletter_md,
                       file_name=f"경제뉴스레터_{period_text(start_dt, end_dt)}.md", mime="text/markdown")

    for n, (kw, data, arts) in enumerate(items, 1):
        with st.container(border=True):
            if "error" in data:
                st.error(f"[{kw}] 원고 생성 실패: {data['error'][:300]}")
                continue
            st.caption(f"{n}. 키워드 · {kw}")
            st.markdown(f"### {data.get('headline') or kw}")
            st.markdown("**📌 무슨 일이 있었나**")
            st.markdown("\n".join(f"- {f}" for f in data["facts"]))
            st.markdown("**🧭 왜 중요할까**")
            st.write(data.get("why", ""))
            st.markdown("**💬 독자들이 궁금해하는 것**")
            for qa in data["qna"]:
                st.markdown(f"**Q. {qa['q']}**")
                st.markdown(f"> {qa['a']}")
            if data["watch"]:
                st.markdown("**👀 앞으로 볼 포인트**")
                st.markdown("\n".join(f"- {w}" for w in data["watch"]))
            if arts:
                with st.expander("🔗 참고 기사"):
                    for x in arts:
                        st.markdown(f"- [{x['title']}]({x['link']}) · {x['source']} · {x['published']}")
else:
    st.info("👈 사이드바에서 키워드 개수를 조정한 후, **[🚀 뉴스 자동 생성 시작]** 버튼을 눌러보세요.")

    with st.container():
        st.markdown("### 📋 파이프라인 처리 흐름 안내")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("1단계", "헤드라인 수집", "분야별 경제 뉴스")
        c2.metric("2단계", "키워드 선별", "핵심 이슈 추출")
        c3.metric("3단계", "기사 수집", "키워드별 상세 뉴스")
        c4.metric("4단계", "대중 관심사", "구글 연관 검색어")
        c5.metric("5단계", "원고 발행", "뉴스레터 + Q&A")
