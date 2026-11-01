"""매일 KST 오전 11시에 자동 실행되는 경제 뉴스레터 배치 생성기
3개 기간('today', 'yesterday', 'week')의 뉴스레터를 한 번에 생성하여 news_data.json에 저장합니다.
"""
import os
import json
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv

from pipeline import (
    extract_keywords,
    fetch_news,
    fetch_search_queries,
    generate_manuscripts_batch,
    build_newsletter_md,
    period_text
)

# .env 로드
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH, override=True)

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_data.json")

_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _format_date(d: datetime) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일 ({_WEEKDAYS[d.weekday()]})"


def process_period(label_name: str, start_dt: datetime, end_dt: datetime, api_key: str, top_n: int = 5, country: str = "KR"):
    print(f"\n==========================================")
    print(f"[{label_name}] 처리 시작: {start_dt:%Y-%m-%d} ~ {end_dt:%Y-%m-%d}")
    print(f"==========================================")
    
    if start_dt.date() == end_dt.date():
        period_label = _format_date(start_dt)
    else:
        span = (end_dt.date() - start_dt.date()).days + 1
        period_label = f"{_format_date(start_dt)} ~ {_format_date(end_dt)} ({span}일간)"

    # 1~2단계: 헤드라인 수집 및 키워드 추출
    print(f"1~2단계: 키워드 추출 중...")
    keywords, info = extract_keywords(top_n, country, start_dt, end_dt, api_key)
    print(f"-> 추출된 키워드: {keywords} (방식: {info.get('method')})")

    if not keywords:
        print("경고: 키워드가 추출되지 않았습니다. 기본 키워드로 대체합니다.")
        keywords = ["코스피", "기준금리", "환율", "부동산", "수출"][:top_n]

    # 3단계: 기사 및 연관 검색어 수집
    print(f"3~4단계: 뉴스 기사 및 구글 연관 검색어 수집 중...")
    news = {}
    queries = {}
    entries = []
    for kw in keywords:
        news[kw] = fetch_news(kw, country, start_dt, end_dt, limit=6)
        queries[kw] = fetch_search_queries(kw, country, max_items=10)
        print(f"  - '{kw}': 기사 {len(news[kw])}건, 연관 검색어 {len(queries[kw])}건")
        entries.append({
            "keyword": kw,
            "articles": news[kw],
            "queries": queries[kw]
        })

    # 4~5단계: Gemini 뉴스레터 원고 일괄 생성
    print(f"5단계: Gemini 뉴스레터 원고 생성 중...")
    results = {}
    if api_key:
        try:
            results = generate_manuscripts_batch(entries, start_dt, end_dt, api_key)
            print("-> Gemini 뉴스레터 생성 완료")
        except Exception as e:
            print(f"-> Gemini 호출 실패 ({e}). 규칙 기반 원고로 폴백합니다.")
            traceback.print_exc()

    # 결과 폴백 보정 (만약 Gemini가 일부 또는 전체 실패한 경우)
    for kw in keywords:
        if kw not in results or "error" in results[kw]:
            arts = news.get(kw, [])
            facts = [a["title"] for a in arts[:3]] if arts else [f"{kw} 관련 시장 동향과 정책 발표가 이어지고 있습니다."]
            results[kw] = {
                "headline": f"{kw} 시장 핵심 이슈 점검",
                "facts": facts,
                "why": f"{kw} 변동은 개인 자산 관리와 금융 시장 전반에 직접적인 영향을 미칩니다.",
                "watch": [f"{kw} 관련 정부 및 중앙은행 발표", "글로벌 시장 거시 지표 변화"],
                "qna": [
                    {"q": f"{kw} 이슈가 개인에게 미치는 실질적인 영향은?", "a": "금리와 환율, 투자 자산 가치에 시차를 두고 반영되므로 포트폴리오 점검이 필요합니다."},
                    {"q": f"앞으로 시장에서 가장 주목할 관전 포인트는?", "a": "공식 정책 지표와 주요 기업 실적 발표에 따라 단기 변동성이 결정될 수 있습니다."},
                    {"q": f"초보 투자자가 지금 주의해야 할 행동은?", "a": "과도한 레버리지를 피하고 분할 접근 관점을 유지하는 것이 바람직합니다."}
                ]
            }

    # 전체 마크다운 문서 생성
    items = [(kw, results[kw], news[kw]) for kw in keywords]
    newsletter_md = build_newsletter_md(items, start_dt, end_dt, datetime.now())

    return {
        "period_label": period_label,
        "start_date": f"{start_dt:%Y-%m-%d}",
        "end_date": f"{end_dt:%Y-%m-%d}",
        "keywords": keywords,
        "results": results,
        "news": news,
        "queries": queries,
        "newsletter_md": newsletter_md,
        "generation_mode": info.get("mode", "fallback")
    }


def run_batch(only=None):
    """only: None(전체) 또는 ['today', 'yesterday', 'week'] 중 일부만 재생성 (기존 JSON에 병합)"""
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    now = datetime.now()
    today_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_dt = today_dt - timedelta(days=1)
    week_start_dt = today_dt - timedelta(days=6)
    targets = only or ["today", "yesterday", "week"]

    print(f"=== [경제 뉴스 AI 배치 생성 시작] ===")
    print(f"실행 시각: {now.strftime('%Y-%m-%d %H:%M:%S')} (API Key 유효: {bool(api_key)}) / 대상: {targets}")

    output = {}
    if only and os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            output = json.load(f)

    if "today" in targets:
        output["today"] = process_period("오늘", today_dt, today_dt, api_key)
    if "yesterday" in targets:
        output["yesterday"] = process_period("어제", yesterday_dt, yesterday_dt, api_key)
    if "week" in targets:
        output["week"] = process_period("지난 일주일", week_start_dt, today_dt, api_key)
    output["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n==========================================")
    print(f"[OK] 배치 완료: {DATA_PATH} 저장 성공!")
    print(f"==========================================")



if __name__ == "__main__":
    import sys
    run_batch(sys.argv[1:] or None)

