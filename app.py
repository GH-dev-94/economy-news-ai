"""경제 뉴스 AI 데일리 브리핑 (WordPress 임베드 & 단독 뷰어)
매일 오전 11시(KST) 자동 생성된 '오늘 / 어제 / 지난 일주일' 뉴스레터를 0.1초 만에 즉시 표출합니다.
"""
import os
import sys
import json
from datetime import datetime
from streamlit.runtime import exists
import streamlit as st

# =============================================================================
# VS Code '▶(실행)' 버튼 또는 'python app.py' 실행 시 자동 streamlit run 전환
# =============================================================================
if __name__ == "__main__" and not exists():
    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", sys.argv[0]]
    sys.exit(stcli.main())

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_data.json")

# =============================================================================
# 페이지 설정 & UI 스타일링 (임베드 친화적 디자인)
# =============================================================================
st.set_page_config(
    page_title="경제 뉴스 AI 데일리 브리핑",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 커스텀 CSS (워드프레스 임베드 시 최적화된 마진과 세련된 카드 디자인)
st.markdown("""
<style>
    /* 여백 최적화 */
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 2rem;
        max-width: 1000px;
    }
    .header-box {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        border-bottom: 2px solid #3b82f6;
        padding-bottom: 0.5rem;
        margin-bottom: 1.2rem;
    }
    .header-title {
        font-size: 1.8rem;
        font-weight: 800;
        color: #1e293b;
        margin: 0;
    }
    @media (prefers-color-scheme: dark) {
        .header-title { color: #f1f5f9; }
    }
    .update-badge {
        font-size: 0.82rem;
        color: #64748b;
        background: rgba(100, 116, 139, 0.1);
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
    }
    .news-card {
        border-left: 4px solid #3b82f6;
        padding-left: 0.8rem;
        margin-bottom: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)


def load_news_data():
    if not os.path.exists(DATA_PATH):
        return None
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        st.error(f"데이터 파일을 읽는 중 오류가 발생했습니다: {e}")
        return None


data = load_news_data()

# 헤더 영역
st.markdown("""
<div class="header-box">
    <div>
        <h1 class="header-title">📈 경제 뉴스 AI 데일리 브리핑</h1>
        <div style="font-size: 0.95rem; color: #64748b; margin-top: 0.2rem;">
            매일 오전 11시(KST), 가장 뜨거운 경제 이슈와 대중 관심사 Q&A를 자동 브리핑합니다.
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

if not data:
    st.warning("⚠️ 아직 생성된 뉴스레터 데이터가 없습니다.")
    st.info("터미널에서 `python batch_generate.py`를 실행하여 초기 데이터를 생성하거나, 매일 오전 11시 GitHub Actions 자동 배치를 기다려주세요.")
    st.stop()

updated_at = data.get("updated_at", "최근")
st.caption(f"🕒 **최근 자동 업데이트:** {updated_at}")

# =============================================================================
# 기간 선택 탭 (오늘 / 어제 / 지난 일주일) -> 0.1초 즉시 전환
# =============================================================================
tab_today, tab_yesterday, tab_week = st.tabs([
    "📅 오늘",
    "⏳ 어제",
    "🗓️ 지난 일주일"
])

tabs_map = {
    tab_today: ("today", data.get("today")),
    tab_yesterday: ("yesterday", data.get("yesterday")),
    tab_week: ("week", data.get("week"))
}


def render_period_content(period_key: str, period_data: dict):
    if not period_data:
        st.info("해당 기간의 브리핑 데이터가 준비되지 않았습니다.")
        return

    period_label = period_data.get("period_label", "")
    keywords = period_data.get("keywords", [])
    results = period_data.get("results", {})
    news = period_data.get("news", {})
    newsletter_md = period_data.get("newsletter_md", "")

    # 상단 요약 바
    c1, c2 = st.columns([3, 1])
    with c1:
        kw_badges = " ".join([f"`{kw}`" for kw in keywords])
        st.markdown(f"**기준 기간:** {period_label}  |  **주요 키워드:** {kw_badges}")
    with c2:
        if newsletter_md:
            st.download_button(
                label="📥 뉴스레터 복사/다운로드 (.md)",
                data=newsletter_md,
                file_name=f"경제뉴스레터_{period_data.get('start_date','data')}.md",
                mime="text/markdown",
                use_container_width=True
            )

    st.markdown("---")

    # 각 키워드별 카드 렌더링
    for idx, kw in enumerate(keywords, 1):
        item = results.get(kw, {})
        arts = news.get(kw, [])

        with st.container(border=True):
            st.caption(f"이슈 {idx} · 키워드: **{kw}**")
            st.markdown(f"### {item.get('headline') or kw}")

            # 1. 무슨 일이 있었나 (불릿 요약)
            st.markdown("#### 📌 무슨 일이 있었나")
            facts = item.get("facts", [])
            if isinstance(facts, list):
                st.markdown("\n".join(f"- {f}" for f in facts))
            else:
                st.markdown(f"- {facts}")

            # 2. 왜 중요할까
            st.markdown("#### 🧭 왜 중요할까")
            st.info(item.get("why", ""))

            # 3. 독자들이 궁금해하는 것 (Q&A 3선)
            st.markdown("#### 💬 독자들이 궁금해하는 핵심 Q&A")
            qna = item.get("qna", [])
            for q_idx, qa in enumerate(qna, 1):
                st.markdown(f"**Q{q_idx}. {qa.get('q')}**")
                st.markdown(f"> **A.** {qa.get('a')}")
                if q_idx < len(qna):
                    st.write("")

            # 4. 앞으로 볼 포인트
            watch = item.get("watch", [])
            if watch:
                st.markdown("#### 👀 앞으로 볼 포인트")
                if isinstance(watch, list):
                    st.markdown("\n".join(f"- {w}" for w in watch))
                else:
                    st.markdown(f"- {watch}")

            # 5. 참고 기사 목록 (아코디언)
            if arts:
                with st.expander(f"🔗 '{kw}' 관련 주요 기사 원문 보기 ({len(arts)}건)"):
                    for a in arts:
                        pub = f" · {a.get('published')}" if a.get('published') else ""
                        src = f" · {a.get('source')}" if a.get('source') else ""
                        st.markdown(f"- [{a.get('title')}]({a.get('link')}){src}{pub}")


# 탭별 렌더링
with tab_today:
    render_period_content(*tabs_map[tab_today])

with tab_yesterday:
    render_period_content(*tabs_map[tab_yesterday])

with tab_week:
    render_period_content(*tabs_map[tab_week])

# 하단 푸터 (임베드용 심플 안내)
st.markdown("---")
st.caption("※ 본 브리핑은 매일 오전 11시(KST) AI가 공개 경제 뉴스 헤드라인과 구글 연관 검색어를 종합 분석하여 자동 발행하며, 특정 종목이나 상품에 대한 투자 권유가 아닙니다.")
