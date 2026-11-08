"""4시간 주기(00, 04, 08, 12, 16, 20시 KST)로 자동 실행되는 경제 뉴스레터 배치 생성기
- 00시(또는 날짜 변경 시): 'today', 'yesterday', 'week' 전체 갱신
- 그 외 4시간 주기: 'today' 집중 갱신 (+ 기존 yesterday/week 데이터가 오염·누락된 경우 자동 복구)
"""
import html as _html
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

KST = timezone(timedelta(hours=9))

from pipeline import (
    _dynamic_cluster_keywords,
    build_newsletter_md,
    extract_dual_keywords,
    fetch_domestic_pool,
    fetch_global_pool,
    fetch_news,
    fetch_search_queries,
    generate_manuscripts_batch,
)

# .env 로드
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH, override=True)

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_data.json")

_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

_BAD_MARKERS = (
    "수집된 기사가 없어",
    "데이터가 존재하지 않아",
    "확인 불가",
    "추가적인 데이터 확보",
    "기본 개념과",
    "기준금리의 정의와",
    "시세 확인법과 전자계약",
    "핵심 보도 종합",
    "관련 보도가 잇따르며 시장 참여자들의 관심이 집중되고 있다",
)


def _format_date(d: datetime) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일 ({_WEEKDAYS[d.weekday()]})"


def _is_valid_period_data(period_data: dict) -> bool:
    """해당 기간의 뉴스레터 데이터가 실제 뉴스 기사 기반으로 정상 생성되었는지 품질 검증."""
    if not isinstance(period_data, dict):
        return False
    if period_data.get("generation_mode") != "gemini":
        return False
    md = period_data.get("newsletter_md") or ""
    if not md or any(marker in md for marker in _BAD_MARKERS):
        return False
    news_map = period_data.get("news") or {}
    if not isinstance(news_map, dict):
        return False
    total_articles = sum(len(v) for v in news_map.values() if isinstance(v, list))
    return total_articles >= 6


def _build_article_based_fallback(kw: str, arts: list[dict]) -> dict:
    """Gemini 쿼터 초과 등 호출 실패 시에도 상식 창작이 아닌 실제 수집된 기사 제목들로만 요약 구성."""
    facts = []
    for a in arts[:5]:
        clean_t = re.sub(r"\s+-\s+[^-]+$", "", a.get("title", "")).strip()
        if clean_t:
            facts.append(clean_t)
    if not facts:
        facts = [f"{kw} 관련 주요 시장 지표 변동 및 후속 논의 진행"]

    first_title = re.sub(r"\s+-\s+[^-]+$", "", arts[0]["title"]).strip() if arts else kw
    short_headline = first_title[:24] if len(first_title) <= 24 else f"{kw} 핵심 보도 종합"

    return {
        "headline": short_headline,
        "facts": facts,
        "watch": [
            f"{kw} 관련 후속 보도 및 시장 수급 추이",
            "주요 거시경제 지표 및 당국 정책 발표 여부",
        ],
        "qna": [
            {
                "q": f"이번 '{kw}' 이슈가 시장에서 부각된 핵심 배경은 무엇인가?",
                "a": f"최근 주요 언론에서 '{first_title}' 등 관련 보도가 잇따르며 시장 참여자들의 관심이 집중되고 있다.",
            },
            {
                "q": f"'{kw}' 흐름이 개인 투자자와 가계 경제에 미치는 영향은 무엇인가?",
                "a": "관련 산업군 주가와 환율, 시중 금리 및 체감 경기에 직간접적인 변동 요인으로 작용할 수 있어 면밀한 관찰이 필요하다.",
            },
            {
                "q": "향후 시장 대응에 있어 어떤 점을 중점적으로 확인해야 하는가?",
                "a": "단기적인 뉴스 헤드라인에 일희일비하기보다 실제 발표되는 기업 실적과 공식 경제 지표를 함께 점검하는 신중한 접근이 요구된다.",
            },
        ],
    }


def process_period(
    label_name: str,
    start_dt: datetime,
    end_dt: datetime,
    api_key: str,
    top_n: int = 3,
    country: str = "KR",
):
    print(f"\n==========================================")
    print(f"[{label_name}] 처리 시작: {start_dt:%Y-%m-%d} ~ {end_dt:%Y-%m-%d}")
    print(f"==========================================")

    if start_dt.date() == end_dt.date():
        period_label = _format_date(start_dt)
    else:
        span = (end_dt.date() - start_dt.date()).days + 1
        period_label = f"{_format_date(start_dt)} ~ {_format_date(end_dt)} ({span}일간)"

    # 1~2단계: 실시간 국내/해외 기사 풀 수집 및 이슈 클러스터링 (사전 고정 키워드 없음)
    print("1~2단계: 국내 및 해외 기사 풀 수집 & 실시간 핫이슈 키워드 추출 중...")
    dom_kws, gl_kws, info = extract_dual_keywords(
        n_domestic=top_n,
        n_global=top_n,
        start=start_dt,
        end=end_dt,
        api_key=api_key,
    )

    dom_pool = fetch_domestic_pool(start_dt, end_dt)
    gl_pool = fetch_global_pool(start_dt, end_dt)

    if not dom_kws:
        dom_kws = _dynamic_cluster_keywords([a["title"] for a in dom_pool], topn=top_n)
    if not gl_kws:
        gl_kws = _dynamic_cluster_keywords([a["title"] for a in gl_pool], topn=top_n)

    print(f"-> [국내] 키워드: {dom_kws} (수집 기사 풀: {len(dom_pool)}건)")
    print(f"-> [해외] 키워드: {gl_kws} (수집 기사 풀: {len(gl_pool)}건 / 방식: {info.get('method')})")

    all_kws = dom_kws + gl_kws

    # 3단계: 키워드별 기사 매칭 + 추가 RSS 수집 및 시사 연관 검색어 수집
    print(f"3~4단계: 키워드별 뉴스 기사 및 연관 검색어 수집 중 (총 {len(all_kws)}개)...")
    news = {}
    queries = {}
    entries = []
    for kw in all_kws:
        arts = fetch_news(kw, country, start_dt, end_dt, limit=6)
        # 만약 특정 키워드에 매칭된 기사가 0건이면 해당 섹션 기사 풀 상위 기사로 보완
        if not arts:
            target_pool = dom_pool if kw in dom_kws else gl_pool
            arts = [
                {
                    "title": a["title"],
                    "link": a.get("link", ""),
                    "source": a.get("source", ""),
                    "published": a.get("published", ""),
                    "snippet": a.get("snippet", ""),
                }
                for a in target_pool[:5]
            ]
        news[kw] = arts
        queries[kw] = fetch_search_queries(kw, country, max_items=8) if len(arts) >= 2 else []
        print(f"  - '{kw}': 기사 {len(news[kw])}건, 연관 검색어 {len(queries[kw])}건")
        entries.append({
            "keyword": kw,
            "articles": news[kw],
            "queries": queries[kw],
        })

    # 4~5단계: Gemini 뉴스레터 원고 일괄 생성
    print("5단계: Gemini 뉴스레터 원고 일괄 생성 중...")
    results = {}
    if api_key and entries:
        try:
            results = generate_manuscripts_batch(entries, start_dt, end_dt, api_key)
            print("-> Gemini 뉴스레터 생성 완료")
        except Exception as e:
            print(f"-> Gemini 호출 실패 ({e}). 실제 수집 기사 기반 요약으로 폴백합니다.")
            traceback.print_exc()

    # 결과 검증 및 실제 기사 기반 폴백 보정
    fallback_count = 0
    for kw in all_kws:
        is_bad = False
        if kw not in results or "error" in results[kw]:
            is_bad = True
        else:
            facts = results[kw].get("facts", [])
            facts_text = " ".join(facts)
            headline_text = results[kw].get("headline", "")
            if any(marker in facts_text or marker in headline_text for marker in _BAD_MARKERS):
                is_bad = True

        if is_bad:
            fallback_count += 1
            results[kw] = _build_article_based_fallback(kw, news.get(kw, []))

    dom_items = [(kw, results[kw], news[kw]) for kw in dom_kws]
    gl_items = [(kw, results[kw], news[kw]) for kw in gl_kws]
    newsletter_md = build_newsletter_md(dom_items, gl_items, start_dt, end_dt, datetime.now(KST).replace(tzinfo=None))

    gen_mode = "fallback" if fallback_count > 0 else info.get("mode", "fallback")

    return {
        "period_label": period_label,
        "start_date": f"{start_dt:%Y-%m-%d}",
        "end_date": f"{end_dt:%Y-%m-%d}",
        "domestic": {
            "keywords": dom_kws,
            "results": {kw: results[kw] for kw in dom_kws},
            "news": {kw: news[kw] for kw in dom_kws},
            "queries": {kw: queries[kw] for kw in dom_kws},
        },
        "global": {
            "keywords": gl_kws,
            "results": {kw: results[kw] for kw in gl_kws},
            "news": {kw: news[kw] for kw in gl_kws},
            "queries": {kw: queries[kw] for kw in gl_kws},
        },
        "keywords": all_kws,
        "results": results,
        "news": news,
        "queries": queries,
        "newsletter_md": newsletter_md,
        "generation_mode": gen_mode,
    }


def run_batch(only=None):
    """only: None(스마트 자동 판단) 또는 ['today', 'yesterday', 'week'] 중 일부만 재생성"""
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    now = datetime.now(KST).replace(tzinfo=None)
    today_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_dt = today_dt - timedelta(days=1)
    week_start_dt = yesterday_dt - timedelta(days=6)  # 어제 기준 과거 7일간 (yesterday_dt - 6일 ~ yesterday_dt)

    output = {}
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                output = json.load(f)
        except Exception:
            output = {}

    if only:
        if "all" in only:
            targets = ["today", "yesterday", "week"]
        else:
            targets = only
    else:
        # 스마트 4시간 주기 자동 판단 + 오염 데이터 자동 복구(Self-Healing):
        # 1. 00시대 실행이거나, 날짜가 바뀌어 어제 날짜가 일치하지 않는 경우 -> 전체 갱신
        # 2. 그 외 시간(04, 08, 12, 16, 20시) -> 기본적으로 'today'만 갱신하되,
        #    기존 'yesterday'나 'week' 데이터가 기사 누락/오염 상태라면 자동 감지하여 함께 복구!
        prev_yesterday = output.get("yesterday", {}).get("start_date")
        expected_yesterday = f"{yesterday_dt:%Y-%m-%d}"
        has_all_keys = all(k in output for k in ["today", "yesterday", "week"])
        is_midnight_slot = (now.hour == 0)

        if not has_all_keys or prev_yesterday != expected_yesterday or is_midnight_slot:
            reason = (
                "자정 00시 정기 풀 갱신"
                if is_midnight_slot
                else f"새 날짜/초기 상태 감지(기존: {prev_yesterday} -> 현재: {expected_yesterday})"
            )
            print(f"[스마트 주기] {reason} -> 전체(today, yesterday, week) 갱신")
            targets = ["today", "yesterday", "week"]
        else:
            targets = ["today"]
            if not _is_valid_period_data(output.get("yesterday")):
                print("[자동 복구] 'yesterday' 데이터의 기사 누락/품질 이상 감지 -> 갱신 대상에 추가")
                targets.append("yesterday")
            if not _is_valid_period_data(output.get("week")) or output.get("week", {}).get("end_date") != expected_yesterday:
                print("[자동 복구] 'week' 데이터의 기간 불일치 또는 품질 이상 감지 -> 갱신 대상에 추가")
                targets.append("week")
            print(f"[스마트 주기] 당일 4시간 간격 갱신 (현재 시각: {now.hour}시) -> 대상: {targets}")

    print("=== [경제 뉴스 AI 배치 생성 시작] ===")
    print(f"실행 시각: {now.strftime('%Y-%m-%d %H:%M:%S')} (API Key 유효: {bool(api_key)}) / 대상: {targets}")

    period_configs = {
        "today": ("오늘", today_dt, today_dt),
        "yesterday": ("어제", yesterday_dt, yesterday_dt),
        "week": ("지난 일주일", week_start_dt, yesterday_dt),
    }

    for key in targets:
        if key not in period_configs:
            continue
        label_name, s_dt, e_dt = period_configs[key]
        new_data = process_period(label_name, s_dt, e_dt, api_key)

        # 품질 게이트(Quality Gate): 만약 네트워크 완전 단절로 새 데이터에 기사가 0건인데
        # 기존에 유효한 데이터가 있었다면 덮어쓰지 않고 기존 정상 데이터를 보호
        new_articles_cnt = sum(len(v) for v in new_data.get("news", {}).values())
        if new_articles_cnt == 0 and _is_valid_period_data(output.get(key)):
            print(f"[품질 게이트] '{key}' 신규 수집 기사가 0건이므로 기존 정상 데이터를 유지합니다.")
            continue

        output[key] = new_data

    output["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    _sync_static_seo_files(output, now)

    print(f"\n==========================================")
    print(f"[OK] 배치 완료: {DATA_PATH} 저장 성공!")
    print(f"==========================================")


def _sync_static_seo_files(output: dict, now: datetime):
    """검색엔진(SEO) 및 애드센스 크롤러가 정적 HTML에서도 전체 브리핑 본문을 읽을 수 있도록 index.html 및 sitemap.xml 갱신."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    index_path = os.path.join(base_dir, "index.html")
    sitemap_path = os.path.join(base_dir, "sitemap.xml")

    if os.path.exists(index_path):
        try:
            today_data = output.get("today", {})
            dom_data = today_data.get("domestic", {})
            dom_kws = dom_data.get("keywords", [])
            dom_res = dom_data.get("results", {})
            dom_news = dom_data.get("news", {})

            cards_html = []
            for idx, kw in enumerate(dom_kws, 1):
                item = dom_res.get(kw, {})
                arts = dom_news.get(kw, [])
                headline = _html.escape(item.get("headline") or f"{kw} 시장 핵심 이슈")
                facts = item.get("facts") or []
                facts_li = "".join(f"<li>{_html.escape(str(f))}</li>" for f in facts)
                qna = item.get("qna") or []
                qna_div = "".join(
                    f'<div class="qna-item"><div class="qna-q">Q{qi}. {_html.escape(str(qa.get("q", "")))}</div>'
                    f'<blockquote class="qna-a"><strong>A.</strong> {_html.escape(str(qa.get("a", "")))}</blockquote></div>'
                    for qi, qa in enumerate(qna, 1)
                )
                watch = item.get("watch") or []
                watch_ul = (
                    '<h4 class="section-h4">👀 앞으로 볼 포인트</h4><ul class="bullet-list">'
                    + "".join(f"<li>{_html.escape(str(w))}</li>" for w in watch)
                    + "</ul>"
                    if watch else ""
                )
                arts_ul = (
                    f'<details class="articles-details"><summary>🔗 관련 주요 기사 원문 ({len(arts)}건)</summary><ul class="articles-list">'
                    + "".join(
                        f'<li><a href="{_html.escape(str(a.get("link", "#")))}" target="_blank" rel="noopener">'
                        f'{_html.escape(str(a.get("title", "")))}</a></li>'
                        for a in arts
                    )
                    + "</ul></details>"
                    if arts else ""
                )
                cards_html.append(
                    f'<article class="expander-card" id="card-{idx}">'
                    f'<button class="expander-header" onclick="toggleExpander(this)">'
                    f'<div class="expander-title-wrap"><span class="chevron">❯</span>'
                    f'<span>📌 {idx}. {headline}</span><span class="kw-badge">{_html.escape(kw)}</span></div></button>'
                    f'<div class="expander-body"><h4 class="section-h4">📌 무슨 일이 있었나</h4>'
                    f'<ul class="bullet-list">{facts_li}</ul><h4 class="section-h4">💬 핵심 Q&amp;A</h4>'
                    f'{qna_div}{watch_ul}{arts_ul}</div></article>'
                )

            with open(index_path, "r", encoding="utf-8") as f:
                html_text = f.read()

            replacement = "<!-- PRE_RENDERED_START -->\n      " + "\n      ".join(cards_html) + "\n      <!-- PRE_RENDERED_END -->"
            html_text = re.sub(
                r"<!-- PRE_RENDERED_START -->.*?<!-- PRE_RENDERED_END -->",
                lambda _: replacement,
                html_text,
                flags=re.DOTALL,
            )
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(html_text)
        except Exception as e:
            print(f"[경고] index.html 프리렌더링 동기화 실패: {e}")

    if os.path.exists(sitemap_path):
        try:
            with open(sitemap_path, "r", encoding="utf-8") as f:
                sm = f.read()
            sm = re.sub(r"<lastmod>.*?</lastmod>", f"<lastmod>{now:%Y-%m-%d}</lastmod>", sm)
            with open(sitemap_path, "w", encoding="utf-8") as f:
                f.write(sm)
        except Exception as e:
            print(f"[경고] sitemap.xml 갱신 실패: {e}")


if __name__ == "__main__":
    run_batch(sys.argv[1:] or None)
