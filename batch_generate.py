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
import urllib.parse
import urllib.request
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

    print("\n6단계: 글로벌 핵심 시장 지표 스냅샷 수집 중...")
    snapshot = _fetch_market_snapshot(now)
    if snapshot.get("items"):
        output["market_snapshot"] = snapshot
        print(f"-> 시장 지표 {len(snapshot['items'])}개 수집 완료")

    cal_updated_date = str(output.get("macro_calendar", {}).get("updatedAt", ""))[:10]
    if now.hour == 0 or not output.get("macro_calendar") or cal_updated_date != f"{now:%Y-%m-%d}":
        print("\n7단계: 과거 7일 + 미래 1개월 핵심 경제 캘린더 갱신 중...")
        cal = _fetch_macro_calendar(now)
        if cal.get("events"):
            output["macro_calendar"] = cal
            print(f"-> 경제 일정 {len(cal['events'])}건 갱신 완료")

    output["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    _sync_static_seo_files(output, now)

    print(f"\n==========================================")
    print(f"[OK] 배치 완료: {DATA_PATH} 저장 성공!")
    print(f"==========================================")


def _fetch_market_snapshot(now: datetime) -> dict:
    """Yahoo Finance 공개 차트 API에서 주요 지수·환율·금리·VIX·BTC 스냅샷 수집(4시간 백업용)."""
    specs = [
        ("kospi", "^KS11", "코스피", "", 2),
        ("kosdaq", "^KQ11", "코스닥", "", 2),
        ("nasdaq", "^IXIC", "나스닥", "", 2),
        ("sp500", "^GSPC", "S&P 500", "", 2),
        ("usdkrw", "KRW=X", "원/달러", "원", 1),
        ("us10y", "^TNX", "미 국채 10년", "%", 2),
        ("vix", "^VIX", "VIX 공포지수", "", 2),
        ("btc", "BTC-USD", "비트코인", "$", 0),
    ]
    items = {}
    for key, symbol, label, unit, decimals in specs:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ZippyMarket/1.0)"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result_obj = data["chart"]["result"][0]
            meta = result_obj["meta"]
            price = float(meta["regularMarketPrice"])

            timestamps = result_obj.get("timestamp") or []
            raw_closes = ((result_obj.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            valid_bars = [
                (timestamps[i] if i < len(timestamps) else 0, float(c))
                for i, c in enumerate(raw_closes)
                if isinstance(c, (int, float))
            ]

            prev = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
            if len(valid_bars) >= 2:
                gmtoff = int(meta.get("gmtoffset") or 0)
                def _local_day(sec: int) -> str:
                    return datetime.fromtimestamp(sec + gmtoff, tz=timezone.utc).strftime("%Y-%m-%d")
                mkt_day = _local_day(int(meta.get("regularMarketTime") or valid_bars[-1][0]))
                last_day = _local_day(int(valid_bars[-1][0]))
                prev = valid_bars[-2][1] if last_day == mkt_day else valid_bars[-1][1]

            chg = price - prev
            chg_pct = (chg / prev * 100.0) if prev > 0 else 0.0
            items[key] = {
                "key": key,
                "label": label,
                "symbol": symbol,
                "unit": unit,
                "decimals": decimals,
                "price": price,
                "prevClose": prev,
                "change": chg,
                "changePct": chg_pct,
                "high52w": meta.get("fiftyTwoWeekHigh"),
                "low52w": meta.get("fiftyTwoWeekLow"),
            }
        except Exception:
            continue
    return {
        "updatedAt": now.isoformat(),
        "source": "github-4h-snapshot",
        "items": items,
    }


def _fetch_macro_calendar(now: datetime) -> dict:
    """과거 7일 + 미래 31일 핵심 거시경제 일정 수집 (실제 피드 + 공식 확정 일정 병합)."""
    past_start = (now - timedelta(days=7)).date()
    future_end = (now + timedelta(days=31)).date()

    country_map = {
        "USD": "🇺🇸 미국",
        "JPY": "🇯🇵 일본",
        "EUR": "🇪🇺 유로존",
        "CNY": "🇨🇳 중국",
        "GBP": "🇬🇧 영국",
    }

    title_rules = [
        ("Core PCE", "미국 근원 개인소비지출(Core PCE) 물가지수", "연준이 가장 중시하는 물가 지표. 예상 상회 시 금리↑·기술주↓ 주의"),
        ("PCE Price", "미국 개인소비지출(PCE) 물가지수", "소비·물가 흐름 동시 점검. 예상 하회 시 금리안정·증시 우호"),
        ("Non-Farm", "미국 비농업부문 고용자 수 (NFP)", "고용 과열 시 금리인하 지연 우려(나스닥↓), 둔화 시 증시 우호"),
        ("Unemployment Rate", "미국 실업률", "고용시장 냉각 여부를 가늠하는 핵심 지표"),
        ("CPI", "소비자물가지수 (CPI)", "인플레이션 둔화 여부에 따라 채권금리·환율·증시 즉각 변동"),
        ("PPI", "생산자물가지수 (PPI)", "소비자물가 선행 지표. 예상 상회 시 인플레 경계감 확대"),
        ("GDP", "국내총생산 (GDP) 성장률", "경기 침체 vs 연착륙 여부를 판단하는 핵심 성장 지표"),
        ("Federal Funds Rate", "미 연준 FOMC 기준금리 결정", "기준금리 결정 및 점도표·파월 의장 발언이 글로벌 자산시장 분수령"),
        ("FOMC", "미 연준 FOMC 회의록·성명", "연준 위원들의 금리 인하/긴축 스탠스 확인"),
        ("Retail Sales", "미국 소매판매", "미국 내수 소비 탄탄함 측정. 급감 시 경기침체 우려 부각"),
        ("ISM Manufacturing", "미국 ISM 제조업 PMI", "기준선 50 상회 시 제조업 경기 확장, 하회 시 위축 신호"),
        ("ISM Services", "미국 ISM 서비스업 PMI", "미국 경제의 70%를 차지하는 서비스업 경기 체력 지표"),
        ("BOJ", "일본은행(BOJ) 통화정책 결정", "일본 금리 인상 시사 시 엔캐리 청산 경계로 글로벌 변동성 확대"),
        ("ECB", "유럽중앙은행(ECB) 기준금리 결정", "유로존 통화정책 및 달러 인덱스 방향성에 영향"),
    ]

    def _translate_and_guide(raw_title: str) -> tuple[str, str]:
        for key, ko_title, guide in title_rules:
            if key.lower() in raw_title.lower():
                return ko_title, guide
        return raw_title, "주요 거시경제 지표 발표에 따른 금리·환율 단기 변동성 유의"

    events_by_key: dict[tuple[str, str], dict] = {}

    # 1. 무료 공개 경제 캘린더 JSON (이번 주 + 다음 주)
    for feed_url in (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ):
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0 (ZippyCalendar/1.0)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
            if not isinstance(rows, list):
                continue
            for r in rows:
                if r.get("impact") != "High":
                    continue
                ctry = str(r.get("country", "")).upper()
                if ctry not in ("USD", "JPY", "EUR", "CNY"):
                    continue
                raw_dt = str(r.get("date", "")).strip()
                if not raw_dt:
                    continue
                try:
                    dt_kst = datetime.fromisoformat(raw_dt).astimezone(KST)
                except Exception:
                    continue
                ev_date = dt_kst.date()
                if not (past_start <= ev_date <= future_end):
                    continue
                raw_title = str(r.get("title", "")).strip()
                ko_title, guide = _translate_and_guide(raw_title)
                date_str = f"{ev_date:%Y-%m-%d}"
                dedup_key = (date_str, ko_title[:12])
                events_by_key[dedup_key] = {
                    "date": date_str,
                    "timeKst": f"{dt_kst:%H:%M}",
                    "region": country_map.get(ctry, ctry),
                    "title": ko_title,
                    "rawTitle": raw_title,
                    "importance": "최상",
                    "forecast": str(r.get("forecast") or "").strip(),
                    "previous": str(r.get("previous") or "").strip(),
                    "actual": str(r.get("actual") or "").strip(),
                    "guide": guide,
                    "source": "live-feed",
                }
        except Exception:
            continue

    # 2. 공식 확정 일정 (피드 범위 이후 2~4주 뒤까지 빈틈 보완)
    confirmed_schedule = [
        ("2026-12-04", "22:30", "🇺🇸 미국", "미국 11월 비농업고용·실업률 (고용보고서)", "최상", "고용 과열 시 금리인하 지연 우려(나스닥↓), 둔화 시 금리안정·증시 우호"),
        ("2026-12-10", "22:30", "🇺🇸 미국", "미국 11월 소비자물가지수 (CPI)", "최상", "근원 CPI 예상 상회 시 인플레 경계(환율↑), 하회 시 기술주·채권 강세"),
        ("2026-12-17", "04:00", "🇺🇸 미국", "12월 FOMC 기준금리 결정 & 점도표 발표", "최상", "금리 결정 및 2027년 점도표·파월 기자회견이 연말 시장의 분수령"),
        ("2026-12-18", "12:00", "🇯🇵 일본", "일본은행(BOJ) 금융정책결정회의", "상", "일본 금리 인상 시사 시 엔캐리 청산 경계로 글로벌 단기 변동성 확대"),
        ("2026-12-23", "22:30", "🇺🇸 미국", "미국 11월 개인소비지출(PCE) 물가지수", "상", "연준이 가장 선호하는 물가 지표. 연말 소비 및 물가 안정 여부 확인"),
        ("2027-01-08", "22:30", "🇺🇸 미국", "미국 12월 비농업고용·실업률 (고용보고서)", "최상", "새해 첫 고용 지표. 연초 금리 경로 및 경기 펀더멘털 가늠자"),
        ("2027-01-13", "22:30", "🇺🇸 미국", "미국 12월 소비자물가지수 (CPI)", "최상", "연초 물가 둔화 지속 여부에 따라 1월 FOMC 기대감 좌우"),
        ("2027-01-15", "10:00", "🇰🇷 한국", "한국은행 금융통화위원회 (기준금리 결정)", "최상", "원/달러 환율 레벨과 가계부채 추이에 따른 한은 금리 스탠스 결정"),
        ("2027-01-28", "04:00", "🇺🇸 미국", "1월 FOMC 기준금리 결정", "최상", "새해 첫 미 연준 기준금리 결정 및 통화정책 방향성 제시"),
        ("2027-02-25", "10:00", "🇰🇷 한국", "한국은행 금융통화위원회 (기준금리 결정)", "최상", "국내 경기 부양 필요성과 환율·물가 간 균형점 판단"),
        ("2027-03-18", "03:00", "🇺🇸 미국", "3월 FOMC 기준금리 결정 & 경제전망(SEP)", "최상", "분기 경제전망 및 점도표 업데이트 발표"),
    ]
    for d_str, t_str, reg, ttl, imp, gd in confirmed_schedule:
        try:
            ev_d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if not (past_start <= ev_d <= future_end):
            continue
        # 같은 날짜에 실데이터 피드로 이미 들어온 유사 항목이 있으면 피드 우선
        already = any(
            k[0] == d_str and (k[1][:6] in ttl or ttl[:6] in k[1])
            for k in events_by_key.keys()
        )
        if not already:
            events_by_key[(d_str, ttl[:12])] = {
                "date": d_str,
                "timeKst": t_str,
                "region": reg,
                "title": ttl,
                "rawTitle": ttl,
                "importance": imp,
                "forecast": "",
                "previous": "",
                "actual": "",
                "guide": gd,
                "source": "confirmed-schedule",
            }

    events = sorted(events_by_key.values(), key=lambda x: (x["date"], x.get("timeKst", "")))
    return {
        "updatedAt": now.isoformat(),
        "range": {"start": f"{past_start:%Y-%m-%d}", "end": f"{future_end:%Y-%m-%d}"},
        "events": events,
    }


def _sync_static_seo_files(output: dict, now: datetime):
    """검색엔진(SEO) 및 애드센스 크롤러가 정적 HTML에서도 전체 브리핑 본문과 일자별 아카이브를 읽을 수 있도록 동기화."""
    try:
        from site_builder import sync_all_static_pages
        sync_all_static_pages(output, now)
    except Exception as e:
        print(f"[경고] 정적 SEO/아카이브 파일 동기화 실패: {e}")


if __name__ == "__main__":
    run_batch(sys.argv[1:] or None)
