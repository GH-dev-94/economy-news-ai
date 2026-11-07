"""경제 뉴스 AI 파이프라인 (실시간 뉴스 클러스터링 & 팩트 기반 생성)
  [Step 1~2] 국내/해외 경제 뉴스 기사 풀(Pool) 다중 수집 및 실시간 핫이슈 키워드 동적 클러스터링
  [Step 3]   기사 풀 매칭 + 키워드별 추가 뉴스 수집 (Google News RSS + 국내 주요 경제지 RSS 폴백)
  [Step 4~5] Gemini로 팩트 기반 이슈 요약 + 시사 Q&A 3선 생성 (사전적 상식·환각 방지)
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
TIMEOUT = 10

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
]

COUNTRY = {
    "KR": {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},
    "US": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}

# Google News RSS 장애/타임아웃 시 백업용 주요 경제 언론사 직접 RSS
BACKUP_DOMESTIC_RSS = [
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("매일경제", "https://www.mk.co.kr/rss/30100041/"),
    ("한국경제", "https://www.hankyung.com/feed/economy"),
]
BACKUP_GLOBAL_RSS = [
    ("매일경제 국제", "https://www.mk.co.kr/rss/30300018/"),
    ("연합뉴스 국제", "https://www.yna.co.kr/rss/international.xml"),
    ("한국경제 국제", "https://www.hankyung.com/feed/international"),
]

# 비뉴스성(사전 정의·포털 기능·단순 시간 조회) 연관 검색어 필터링 단어
NON_NEWS_QUERY_TERMS = (
    " 뜻", "뜻 ", "영어로", "114", "전자계약", "사이트", "개장시간", "마감시간",
    "휴장일", "서머타임", "계산기", "조회 방법", "홈페이지", "고객센터", "앱",
)


def _create_session() -> requests.Session:
    """네트워크 일시 순단 및 429/5xx 에러에 자동 재시도하는 HTTP 세션 생성."""
    session = requests.Session()
    retry = Retry(
        total=3,
        read=2,
        connect=2,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


_SESSION = _create_session()

# 기간별 수집 기사 메모리 풀: {(scope, start_date, end_date): [article_dict, ...]}
_ARTICLE_POOL = {}


def period_text(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%Y-%m-%d}"
    return f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"


def _parse_rss(url: str):
    r = _SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return ET.fromstring(r.content)


def _clean_title_and_source(raw_title: str, raw_source: str = "") -> tuple[str, str]:
    """제목 끝의 ' - 언론사' 꼬리를 정리하고 언론사명을 보완."""
    t = (raw_title or "").strip()
    src = (raw_source or "").strip()
    m = re.search(r"^(.*?)\s+-\s+([^-]+)$", t)
    if m and not src:
        src = m.group(2).strip()
    return t, src


def _parse_rss_items(root, start_d, end_d, tz=KST, default_source: str = "", strict_date: bool = True) -> list[dict]:
    """RSS XML에서 기사 리스트(title, link, source, published, snippet)를 추출."""
    items = []
    seen_titles = set()
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        raw_source = (item.findtext("source") or default_source or "").strip()
        title, src = _clean_title_and_source(raw_title, raw_source)
        norm_title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
        if not norm_title or norm_title in seen_titles:
            continue

        pub = item.findtext("pubDate") or ""
        pub_str = ""
        pub_date = None
        if pub:
            try:
                pub_dt = parsedate_to_datetime(pub).astimezone(tz)
                pub_date = pub_dt.date()
                pub_str = pub_dt.strftime("%m-%d %H:%M")
            except Exception:
                pub_date = None

        if strict_date and pub_date is not None:
            if not (start_d <= pub_date <= end_d):
                continue

        desc_html = item.findtext("description") or ""
        desc = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
        link = (item.findtext("link") or "").strip()

        seen_titles.add(norm_title)
        items.append({
            "title": title,
            "clean_title": norm_title,
            "link": link,
            "source": src,
            "published": pub_str,
            "snippet": desc[:250],
        })
    return items


# -----------------------------------------------------------------------------
# [Step 1~2] 국내/해외 경제 기사 풀(Pool) 수집 및 동적 핫이슈 클러스터링
# -----------------------------------------------------------------------------
def fetch_domestic_pool(start: datetime = None, end: datetime = None, limit: int = 80) -> list[dict]:
    """국내 경제 뉴스 기사 풀(제목+링크+언론사+발행일)을 다중 쿼리 및 백업 피드에서 수집."""
    start = start or (datetime.now() - timedelta(days=1))
    end = end or datetime.now()
    start_d = (start - timedelta(days=1)).date() if start.date() == end.date() else start.date()
    end_d = end.date()

    pool_key = ("DOM", start.date(), end.date())
    if pool_key in _ARTICLE_POOL and len(_ARTICLE_POOL[pool_key]) >= 15:
        return _ARTICLE_POOL[pool_key]

    c = COUNTRY["KR"]
    after = (start_d - timedelta(days=1)).strftime("%Y-%m-%d")
    before = (end_d + timedelta(days=1)).strftime("%Y-%m-%d")

    # 다양한 경제 분야 쿼리로 폭넓게 기사 수집 (특정 사전 단어장에 갇히지 않도록 분야 전반 포괄)
    queries = [
        f"(한국경제 OR 국내증시 OR 코스피 OR 코스닥 OR 한국은행 OR 금융위 OR 기재부) after:{after} before:{before}",
        f"(부동산 OR 아파트 OR 대출 OR 금리 OR 물가 OR 수출 OR 반도체 OR 환율) after:{after} before:{before}",
        "(한국경제 OR 코스피 OR 금리 OR 부동산 OR 반도체 OR 수출) when:3d",
    ]

    collected = []
    seen = set()

    # 1. Google News 비즈니스 섹션 토픽 RSS
    topic_url = "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"
    try:
        root = _parse_rss(topic_url)
        for art in _parse_rss_items(root, start_d, end_d, strict_date=True):
            if art["clean_title"] not in seen:
                seen.add(art["clean_title"])
                collected.append(art)
    except Exception:
        pass

    # 2. Google News 기간별 검색 쿼리
    for idx, q in enumerate(queries):
        if len(collected) >= limit:
            break
        # 마지막(when:3d) 쿼리는 앞선 날짜 필터 검색 결과가 부족할 때만 실행
        if idx == 2 and len(collected) >= 20:
            continue
        strict = (idx < 2)
        url = f"https://news.google.com/rss/search?q={quote(q)}&hl={c['hl']}&gl={c['gl']}&ceid={c['ceid']}"
        try:
            root = _parse_rss(url)
            for art in _parse_rss_items(root, start_d, end_d, strict_date=strict):
                if art["clean_title"] not in seen:
                    seen.add(art["clean_title"])
                    collected.append(art)
                if len(collected) >= limit:
                    break
        except Exception:
            continue

    # 3. 백업: 구글 뉴스 RSS가 일시 차단/장애로 기사가 부족할 때 국내 주요 경제지 RSS 직접 수집
    if len(collected) < 15:
        for src_name, rss_url in BACKUP_DOMESTIC_RSS:
            try:
                root = _parse_rss(rss_url)
                for art in _parse_rss_items(root, start_d, end_d, default_source=src_name, strict_date=False):
                    if art["clean_title"] not in seen:
                        seen.add(art["clean_title"])
                        collected.append(art)
                    if len(collected) >= limit:
                        break
            except Exception:
                continue

    _ARTICLE_POOL[pool_key] = collected[:limit]
    return _ARTICLE_POOL[pool_key]


def fetch_global_pool(start: datetime = None, end: datetime = None, limit: int = 80) -> list[dict]:
    """해외/글로벌 경제 뉴스 기사 풀(제목+링크+언론사+발행일)을 수집."""
    start = start or (datetime.now() - timedelta(days=1))
    end = end or datetime.now()
    start_d = (start - timedelta(days=1)).date() if start.date() == end.date() else start.date()
    end_d = end.date()

    pool_key = ("GL", start.date(), end.date())
    if pool_key in _ARTICLE_POOL and len(_ARTICLE_POOL[pool_key]) >= 15:
        return _ARTICLE_POOL[pool_key]

    c = COUNTRY["KR"]
    after = (start_d - timedelta(days=1)).strftime("%Y-%m-%d")
    before = (end_d + timedelta(days=1)).strftime("%Y-%m-%d")

    queries = [
        f"(뉴욕증시 OR 나스닥 OR 미국증시 OR 연준 OR FOMC OR 월가 OR 미국고용 OR 미중무역) after:{after} before:{before}",
        f"(글로벌경제 OR 국제유가 OR 달러환율 OR 엔비디아 OR 빅테크 OR 중국경제 OR 관세) after:{after} before:{before}",
        "(뉴욕증시 OR 나스닥 OR 연준 OR 국제유가 OR 글로벌경제) when:3d",
    ]

    collected = []
    seen = set()

    for idx, q in enumerate(queries):
        if len(collected) >= limit:
            break
        if idx == 2 and len(collected) >= 20:
            continue
        strict = (idx < 2)
        url = f"https://news.google.com/rss/search?q={quote(q)}&hl={c['hl']}&gl={c['gl']}&ceid={c['ceid']}"
        try:
            root = _parse_rss(url)
            for art in _parse_rss_items(root, start_d, end_d, strict_date=strict):
                if art["clean_title"] not in seen:
                    seen.add(art["clean_title"])
                    collected.append(art)
                if len(collected) >= limit:
                    break
        except Exception:
            continue

    # 백업: 국제/글로벌 경제 RSS 피드
    if len(collected) < 15:
        for src_name, rss_url in BACKUP_GLOBAL_RSS:
            try:
                root = _parse_rss(rss_url)
                for art in _parse_rss_items(root, start_d, end_d, default_source=src_name, strict_date=False):
                    if art["clean_title"] not in seen:
                        seen.add(art["clean_title"])
                        collected.append(art)
                    if len(collected) >= limit:
                        break
            except Exception:
                continue

    _ARTICLE_POOL[pool_key] = collected[:limit]
    return _ARTICLE_POOL[pool_key]


def fetch_domestic_headlines(start: datetime = None, end: datetime = None, limit: int = 70) -> list[str]:
    return [a["title"] for a in fetch_domestic_pool(start, end, limit)]


def fetch_global_headlines(start: datetime = None, end: datetime = None, limit: int = 70) -> list[str]:
    return [a["title"] for a in fetch_global_pool(start, end, limit)]


def fetch_econ_headlines(country: str = "KR", start: datetime = None, end: datetime = None, limit: int = 60) -> list[str]:
    return fetch_domestic_headlines(start, end, limit)


def _gemini(prompt: str, api_key: str, model_override: str = None) -> str:
    """Gemini REST API 호출."""
    base = "https://generativelanguage.googleapis.com/v1beta"
    hdr = {"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"}
    names = [model_override or os.getenv("GEMINI_MODEL", "")] + GEMINI_MODELS
    try:
        r = _SESSION.get(f"{base}/models?pageSize=200", headers=hdr, timeout=TIMEOUT)
        r.raise_for_status()
        avail = [
            m["name"].replace("models/", "")
            for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
            and "flash" in m["name"]
            and not any(x in m["name"] for x in ("lite", "preview", "tts", "image", "omni", "latest"))
        ]
        names += sorted(avail, reverse=True)
    except Exception:
        pass
    errors, seen = [], set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            r = _SESSION.post(
                f"{base}/models/{name}:generateContent",
                headers=hdr,
                timeout=45,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": 16384,
                        "temperature": 0.2,
                    },
                },
            )
            if r.status_code != 200:
                errors.append(f"{name}: {r.status_code} {r.text[:80]}")
                continue
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            errors.append(f"{name}: {str(e)[:80]}")
    raise RuntimeError("Gemini 호출 실패 - " + " | ".join(errors))


def _extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return json.loads(text[start:])


_STOP = {
    "경제", "뉴스", "오늘", "어제", "속보", "종합", "단독", "포토", "영상", "기자", "전망", "분석",
    "이번", "올해", "내년", "대한", "관련", "위해", "통해", "에서", "으로", "한국", "정부", "시장",
    "업계", "기업", "발표", "기록", "보도", "이슈", "마감", "장중", "오전", "오후", "하루", "다시",
    "이상", "이하", "미만", "초과", "최고", "최대", "최소", "이후", "이전", "가능", "예정", "수준",
    "가운데", "따르면", "대비", "전년", "전월", "동기", "연속", "돌파", "유지", "확대", "축소",
}
_JOSA_MULTI = ("에서", "으로", "에게", "까지", "부터", "이다")
_JOSA_SINGLE = ("은", "는", "이", "가", "을", "를", "의", "에", "도", "로", "와", "과", "만")
_KEEP_NOUNS = {"턱걸이", "일자리", "먹거리", "볼거리", "살거리", "주린이", "모건스탠리", "배터리", "메모리"}


def _tokenize_headline(title: str) -> list[str]:
    clean = re.sub(r"\s+-\s+[^-]+$", "", title)
    clean = re.sub(r"\[.*?\]|\(.*?\)", " ", clean)
    tokens = []
    for tok in re.findall(r"[0-9A-Za-z가-힣%]+", clean):
        if tok not in _KEEP_NOUNS:
            stripped = False
            for j in _JOSA_MULTI:
                if len(tok) >= len(j) + 2 and tok.endswith(j):
                    tok = tok[: -len(j)]
                    stripped = True
                    break
            if not stripped:
                for j in _JOSA_SINGLE:
                    # 3글자 단어의 '이/리' 어미 오제거 방지 (4글자 이상이거나 '은/는/을/를/의'만 3글자 허용)
                    min_len = 3 if j in ("은", "는", "을", "를", "의", "도", "만") else 4
                    if len(tok) >= min_len and tok.endswith(j):
                        tok = tok[: -len(j)]
                        break
        if len(tok) < 2 or tok in _STOP or re.match(r"^[0-9.%+-]+$", tok):
            continue
        tokens.append(tok)
    return tokens


def _dynamic_cluster_keywords(headlines: list[str], topn: int = 3) -> list[str]:
    """사전 정의된 고정 키워드 없이, 실제 수집된 헤드라인들의 빈출 명사구(2어절 이슈 구절)를 동적으로 클러스터링."""
    if not headlines:
        return []

    word_cnt = Counter()
    bigram_cnt = Counter()
    tokenized_list = []

    for h in headlines:
        toks = _tokenize_headline(h)
        tokenized_list.append(toks)
        for t in set(toks):
            word_cnt[t] += 1
        for i in range(len(toks) - 1):
            w1, w2 = toks[i], toks[i + 1]
            if w1 != w2:
                bigram_cnt[f"{w1} {w2}"] += 1

    selected = []
    used_roots = set()

    # 1순위: 2회 이상 등장한 구체적 2어절 이슈 구절 (예: '코스피 급락', '서학개미 매수')
    for phrase, cnt in bigram_cnt.most_common(30):
        if cnt < 2:
            break
        w1, w2 = phrase.split(" ", 1)
        if w1 in used_roots or w2 in used_roots:
            continue
        selected.append(phrase)
        used_roots.add(w1)
        used_roots.add(w2)
        if len(selected) >= topn:
            return selected

    # 2순위: 가장 많이 등장한 핵심 단어 + 함께 가장 자주 등장한 동반 어절 결합
    for word, _ in word_cnt.most_common(25):
        if word in used_roots:
            continue
        companion = Counter()
        for toks in tokenized_list:
            if word in toks:
                for t in toks:
                    if t != word and t not in used_roots:
                        companion[t] += 1
        if companion:
            best_comp = companion.most_common(1)[0][0]
            phrase = f"{word} {best_comp}"
            used_roots.add(best_comp)
        else:
            phrase = word
        selected.append(phrase)
        used_roots.add(word)
        if len(selected) >= topn:
            break

    return selected[:topn]


def extract_keywords(n: int, country: str, start: datetime, end: datetime, api_key: str = ""):
    """단일 국가/범위 키워드 추출 (하위 호환용)."""
    dom_kws, _, info = extract_dual_keywords(n_domestic=n, n_global=0, start=start, end=end, api_key=api_key)
    return dom_kws[:n], info


def extract_dual_keywords(
    n_domestic: int = 3,
    n_global: int = 3,
    start: datetime = None,
    end: datetime = None,
    api_key: str = "",
):
    """수집된 국내/해외 실제 기사 풀을 바탕으로, 가장 많이 보도된 구체적 경제 사건 키워드를 선별."""
    start = start or (datetime.now() - timedelta(days=1))
    end = end or datetime.now()
    ck = ("dual_kw", n_domestic, n_global, start.date(), end.date())
    if api_key and cache_get(ck):
        dom_c, gl_c, info_c = cache_get(ck)
        return dom_c, gl_c, {**info_c, "cached": True}

    dom_pool = fetch_domestic_pool(start, end)
    gl_pool = fetch_global_pool(start, end) if n_global > 0 else []

    dom_headlines = [a["title"] for a in dom_pool]
    gl_headlines = [a["title"] for a in gl_pool]
    info = {"dom_count": len(dom_headlines), "gl_count": len(gl_headlines), "method": ""}

    if api_key and (dom_headlines or gl_headlines):
        prompt = (
            f"{period_text(start, end)} 기간에 실제 보도된 경제 뉴스 헤드라인 목록이야.\n\n"
            f"[🇰🇷 국내 경제 뉴스 헤드라인 ({len(dom_headlines)}건)]\n"
            + "\n".join(f"- {h}" for h in dom_headlines[:55])
            + "\n\n"
            f"[🌐 해외·글로벌 경제 뉴스 헤드라인 ({len(gl_headlines)}건)]\n"
            + "\n".join(f"- {h}" for h in gl_headlines[:55])
            + "\n\n"
            "위 헤드라인들을 클러스터링(군집화)하여, 해당 기간에 실제 기사가 가장 많이 쏟아진 핵심 경제 사건/이슈 키워드를 선정해줘.\n"
            f"- 국내 경제 핵심 이슈 {n_domestic}개\n"
            f"- 해외·글로벌 경제 핵심 이슈 {n_global}개 (국내 이슈와 절대 중복되지 않도록 분리)\n\n"
            "[중요 규칙]\n"
            "1. '코스피', '부동산', '기준금리', '뉴욕증시' 같은 포괄적 단일 명사만 단독으로 쓰지 말고, "
            "반드시 실제 일어난 사건이 드러나는 2~3어절(4~14자)의 구체적인 이슈 구절로 작성할 것. "
            "(좋은 예: '코스피 급락세', '강남 아파트값 하락', '서학개미 매수 폭증', '미국 10월 깜짝 고용', '뉴욕증시 기술주 반등')\n"
            "2. 위 헤드라인 목록에 실제로 최소 3건 이상의 관련 기사가 존재하는 이슈만 선정할 것 (기사에 없는 주제를 임의로 만들지 말 것).\n"
            "3. 키워드에 사용된 핵심 단어는 실제 위 헤드라인들에 등장하는 단어여야 함.\n\n"
            "반드시 아래 JSON 형식으로만 답해:\n"
            '{"domestic": ["국내이슈1", "국내이슈2", "국내이슈3"], "global": ["해외이슈1", "해외이슈2", "해외이슈3"]}'
        )
        try:
            resp = _extract_json(_gemini(prompt, api_key))
            dom_kws = [str(k).strip() for k in resp.get("domestic", []) if str(k).strip()][:n_domestic]
            gl_kws = [str(k).strip() for k in resp.get("global", []) if str(k).strip()][:n_global]
            if dom_kws and (gl_kws or n_global == 0):
                info["mode"] = "gemini"
                info["method"] = "Gemini AI 실시간 헤드라인 클러스터링"
                cache_put(ck, (dom_kws, gl_kws, info))
                return dom_kws, gl_kws, info
        except Exception as e:
            info["gemini_error"] = str(e)
            info["fallback_reason"] = "quota" if "429" in str(e) else "error"

    # 폴백: 사전 고정 단어장 없이 실제 수집된 헤드라인의 빈출 구절 동적 클러스터링
    dom_kws = _dynamic_cluster_keywords(dom_headlines, topn=n_domestic)
    gl_kws = _dynamic_cluster_keywords(gl_headlines, topn=n_global) if n_global > 0 else []
    info["mode"] = "fallback"
    info.setdefault("fallback_reason", "nokey" if not api_key else "nodata")
    info["method"] = "실시간 헤드라인 빈출 이슈 클러스터링"
    return dom_kws[:n_domestic], gl_kws[:n_global], info


# -----------------------------------------------------------------------------
# [Step 3] 기사 풀(Pool) 스마트 매칭 + 키워드별 추가 뉴스 수집
# -----------------------------------------------------------------------------
def _match_from_pool(keyword: str, pool: list[dict], limit: int = 8) -> list[dict]:
    """1단계에서 수집된 기사 풀에서 키워드 어절과 매칭되는 기사를 관련도순으로 추출."""
    if not pool or not keyword:
        return []
    kw_tokens = _tokenize_headline(keyword)
    if not kw_tokens:
        kw_tokens = [w.strip() for w in keyword.split() if len(w.strip()) >= 2]
    if not kw_tokens:
        return []

    scored = []
    for idx, art in enumerate(pool):
        text = f"{art.get('clean_title', '')} {art.get('snippet', '')}"
        title_text = art.get("clean_title", "")
        # 제목에 키워드 핵심 어절이 얼마나 포함되었는지 점수 계산
        title_hits = sum(1 for t in kw_tokens if t in title_text)
        body_hits = sum(1 for t in kw_tokens if t in text)
        if title_hits > 0 or body_hits >= 2:
            score = (title_hits * 3) + body_hits
            scored.append((score, -idx, art))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item[2] for item in scored[:limit]]


def fetch_news(keyword: str, country: str, start: datetime, end: datetime, limit: int = 8) -> list[dict]:
    """1단계 기사 풀 매칭 + Google News RSS 다단계 검색을 결합하여 기사 0건 현상을 방지."""
    c = COUNTRY[country]
    tz = KST if country == "KR" else timezone.utc

    start_d = (start - timedelta(days=1)).date() if start.date() == end.date() else start.date()
    end_d = end.date()

    articles = []
    seen_titles = set()

    def _add_articles(candidates: list[dict]):
        for a in candidates:
            norm = a.get("clean_title") or re.sub(r"\s+-\s+[^-]+$", "", a.get("title", "")).strip()
            if norm and norm not in seen_titles:
                seen_titles.add(norm)
                articles.append({
                    "title": a["title"],
                    "link": a.get("link", ""),
                    "source": a.get("source", ""),
                    "published": a.get("published", ""),
                    "snippet": a.get("snippet", ""),
                })
                if len(articles) >= limit:
                    break

    # 1. 이미 수집된 국내/해외 기사 풀(Pool)에서 우선 매칭 (네트워크 실패 영향 없음)
    dom_pool = _ARTICLE_POOL.get(("DOM", start.date(), end.date()), [])
    gl_pool = _ARTICLE_POOL.get(("GL", start.date(), end.date()), [])
    _add_articles(_match_from_pool(keyword, dom_pool + gl_pool, limit=limit))

    def _rss_search(q_str: str, strict_date: bool = True) -> list[dict]:
        url = (
            f"https://news.google.com/rss/search?q={quote(q_str)}"
            f"&hl={c['hl']}&gl={c['gl']}&ceid={c['ceid']}"
        )
        try:
            root = _parse_rss(url)
            return _parse_rss_items(root, start_d, end_d, tz=tz, strict_date=strict_date)
        except Exception:
            return []

    after = f"{start_d - timedelta(days=1):%Y-%m-%d}"
    before = f"{end_d + timedelta(days=1):%Y-%m-%d}"

    # 2. 기사가 아직 부족하면 Google News RSS 정밀 기간 검색
    if len(articles) < limit:
        _add_articles(_rss_search(f"{keyword} after:{after} before:{before}", strict_date=True))

    # 3. 어절 완화 검색 (예: 3어절 키워드 -> 앞 2어절 또는 핵심 명사 조합)
    if len(articles) < 3:
        words = _tokenize_headline(keyword) or keyword.split()
        if len(words) > 1:
            relaxed_q = " ".join(words[:2])
            _add_articles(_rss_search(f"{relaxed_q} after:{after} before:{before}", strict_date=True))
        if len(articles) < 3 and len(words) >= 1:
            _add_articles(_rss_search(f"{words[0]} 경제 after:{after} before:{before}", strict_date=False))

    # 4. 날짜 필터(after/before)가 구글 RSS에서 누락될 경우를 대비한 최신 기사(when:7d) 폴백
    if len(articles) < 3:
        _add_articles(_rss_search(f"{keyword} when:7d", strict_date=False))

    return articles[:limit]


# -----------------------------------------------------------------------------
# [Step 4~5] AI 분석 + 팩트 기반 뉴스레터 원고 생성
# -----------------------------------------------------------------------------
QUESTION_SUFFIXES = ["", " 이유", " 전망", " 영향"]


def fetch_search_queries(keyword: str, country: str = "KR", max_items: int = 8) -> list[str]:
    """구글 검색 자동완성에서 시사/이슈 관련 검색어만 수집 (단순 뜻풀이·포털 사이트 검색어 제외)."""
    hl = COUNTRY[country]["hl"]
    suffixes = QUESTION_SUFFIXES if country == "KR" else ["", " why", " outlook", " impact"]
    # 키워드가 긴 구절이면 핵심 앞 2어절로도 자동완성 시도
    words = keyword.split()
    base_queries = [keyword]
    if len(words) >= 2:
        base_queries.append(" ".join(words[:2]))

    seen, out = {keyword}, []
    for base_q in base_queries:
        for suf in suffixes:
            try:
                r = _SESSION.get(
                    "https://suggestqueries.google.com/complete/search",
                    params={"client": "firefox", "q": base_q + suf, "hl": hl},
                    timeout=2.5,
                )
                r.raise_for_status()
                for q in r.json()[1]:
                    q = q.strip()
                    if not q or q in seen:
                        continue
                    # 사전적 정의('~ 뜻')나 포털 사이트('부동산114', '개장시간' 등) 검색어는 차단
                    if any(bad in q for bad in NON_NEWS_QUERY_TERMS):
                        continue
                    seen.add(q)
                    out.append(q)
                    if len(out) >= max_items:
                        return out
            except Exception:
                continue
    return out[:max_items]


def generate_manuscript(
    keyword: str,
    articles: list,
    start: datetime,
    end: datetime,
    api_key: str,
    queries: list = None,
):
    """단일 키워드 뉴스레터 꼭지 생성 (하위 호환용)."""
    batch_res = generate_manuscripts_batch(
        [{"keyword": keyword, "articles": articles, "queries": queries or []}],
        start,
        end,
        api_key,
    )
    return batch_res.get(keyword, {})


_CACHE = {}
CACHE_TTL = 30 * 60  # 30분


def cache_get(key):
    v = _CACHE.get(key)
    if v and (datetime.now().timestamp() - v[0]) < CACHE_TTL:
        return v[1]
    return None


def cache_put(key, value):
    _CACHE[key] = (datetime.now().timestamp(), value)


def _strip_media_attribution(text: str) -> str:
    """본문 불릿에서 'OO에 따르면', 'OO는 ~라고 보도했다' 등 불필요한 언론사 인용 표현을 제거."""
    s = (text or "").strip()
    # 1. 문장 앞부분의 'OO(와/및 OO) (보도/자료/집계에) 따르면/의하면,' 패턴 제거
    s = re.sub(
        r"^[가-힣A-Za-z0-9·\.\s,]{1,35}?(?:보도|자료|집계|분석)?에\s*(?:따르면|의하면)[,\s]*",
        "",
        s,
    )
    # 2. 문장 끝이 '~라고 보도했다/전했다/분석했다...' 형태인 경우 앞부분의 언론사 주어('OO와 OO는 ')와 끝의 간접 인용을 함께 정리
    cite_end_pat = r"(?:이?라고|다고|라며)\s*(?:보도했다|전했다|덧붙였다|분석했다|집계했다|전망했다|밝혔다|평가했다|보도함|전함|분석함|집계함)\.?$"
    if re.search(cite_end_pat, s):
        s = re.sub(r"^[가-힣A-Za-z0-9·\.\s]{1,30}?(?:은|는)\s+", "", s)
        s = re.sub(cite_end_pat, "다.", s)
    # 3. '~라는 분석을 내놓았다/전달했다' 형태 정리
    if re.search(r"분석을\s*(?:내놓았다|전달했다|제시했다|보도했다)\.?$", s):
        s = re.sub(r"^[가-힣A-Za-z0-9·\.\s]{1,30}?(?:은|는)\s+", "", s)
        s = re.sub(r"(?:이?라는|다는)\s*분석을\s*(?:내놓았다|전달했다|제시했다|보도했다)\.?$", " 것으로 분석된다.", s)
    return s.strip()


def _normalize_question_tone(q: str) -> str:
    """질문 문체의 어미를 '~입니까?', '~인가요?', '~주나?' 등에서 '~인가?', '~는가?' 체로 일관되게 교정."""
    s = (q or "").strip()
    if not s:
        return s
    replacements = [
        (r"입니까\??$", "인가?"),
        (r"인가요\??$", "인가?"),
        (r"일까요\??$", "인가?"),
        (r"일까\??$", "인가?"),
        (r"합니까\??$", "하는가?"),
        (r"하나요\??$", "하는가?"),
        (r"할까요\??$", "하는가?"),
        (r"할까\??$", "할 것인가?"),
        (r"됩니까\??$", "되는가?"),
        (r"되나요\??$", "되는가?"),
        (r"되나\??$", "되는가?"),
        (r"줍니까\??$", "주는가?"),
        (r"주나요\??$", "주는가?"),
        (r"주나\??$", "주는가?"),
        (r"미치나요\??$", "미치는가?"),
        (r"미치나\??$", "미치는가?"),
        (r"있습니까\??$", "있는가?"),
        (r"있나요\??$", "있는가?"),
        (r"있나\??$", "있는가?"),
        (r"없습니까\??$", "없는가?"),
        (r"없나요\??$", "없는가?"),
        (r"없나\??$", "없는가?"),
        (r"보나요\??$", "보는가?"),
        (r"보나\??$", "보는가?"),
        (r"나타나나요\??$", "나타나는가?"),
        (r"나타나나\??$", "나타나는가?"),
        (r"나요\??$", "는가?"),
        (r"까요\??$", "는가?"),
    ]
    for pat, repl in replacements:
        if re.search(pat, s):
            s = re.sub(pat, repl, s)
            break
    if not s.endswith("?"):
        s += "?"
    return s


_ADJ_STEMS = (
    "필요", "중요", "위험", "유효", "불가피", "바람직", "적절", "유리", "불리",
    "취약", "민감", "불안정", "가능", "불가능", "비슷", "동일", "유사", "확실",
    "불확실", "뚜렷", "극심", "심각", "미미", "충분", "부족", "긴밀", "유력",
    "우세", "저조", "부진", "견조", "탄탄", "안정", "불안", "모호", "명확",
)


def _normalize_declarative_tone(text: str) -> str:
    """답변 및 본문의 존댓말('~입니다', '~합니다', '~습니다')을 간결한 평서문('~이다', '~한다', '~다')으로 일관 교정."""
    s = (text or "").strip()
    if not s:
        return s

    def _conv(m):
        w = m.group(1)
        punct = m.group(2) or "."
        if w.endswith("입니다"):
            return w[:-3] + "이다" + punct
        if w.endswith("아닙니다"):
            return w[:-4] + "아니다" + punct
        if w.endswith("있습니다"):
            return w[:-4] + "있다" + punct
        if w.endswith("없습니다"):
            return w[:-4] + "없다" + punct
        if w.endswith("않습니다"):
            return w[:-4] + "않는다" + punct
        if w.endswith("합니다"):
            stem = w[:-3]
            if stem.endswith(_ADJ_STEMS):
                return stem + "하다" + punct
            return stem + "한다" + punct
        if w.endswith("됩니다"):
            return w[:-3] + "된다" + punct
        if w.endswith("습니다") and len(w) >= 4:
            prev = w[-4]
            code = ord(prev) - 0xAC00
            if 0 <= code <= 11171 and code % 28 == 20:  # ㅆ 받침 과거형 (~았/었/했/됐/졌/났습니다 -> ~다)
                return w[:-3] + "다" + punct
            return w[:-3] + "는다" + punct
        if w.endswith("니다") and len(w) >= 3:
            prev = w[-3]
            code = ord(prev) - 0xAC00
            if 0 <= code <= 11171 and code % 28 == 17:  # ㅂ 받침 현재형 (~듭/립/집/칩/킵니다 -> ~든/린/진/친/킨다)
                new_ch = chr(ord(prev) - 17 + 4)
                return w[:-3] + new_ch + "다" + punct
        return w + punct

    return re.sub(r"(\S+?니다)([.?!]?)", _conv, s)


def generate_manuscripts_batch(entries: list, start: datetime, end: datetime, api_key: str):
    """키워드 여러 개를 Gemini 1회 호출로 한 번에 생성.
    entries: [{"keyword", "articles", "queries"}] -> returns {keyword: data}
    """
    cache_key = ("ms", tuple(e["keyword"] for e in entries), start.date(), end.date())
    hit = cache_get(cache_key)
    if hit:
        return hit

    blocks = []
    for e in entries:
        arts = e.get("articles") or []
        mat_lines = []
        for a in arts:
            clean_t = re.sub(r"\s+-\s+[^-]+$", "", a.get("title", "")).strip()
            pub = f" ({a['published']})" if a.get("published") else ""
            mat_lines.append(f"- {clean_t}{pub}")
        mat = "\n".join(mat_lines) or "(관련 기사 요약 없음)"

        # 기사가 충분히 있을 때만 연관 검색어를 참고 자료로 제공 (기사 부재 시 검색어 기반 소설 작성 방지)
        valid_queries = (e.get("queries") or []) if len(arts) >= 2 else []
        q_str = ", ".join(valid_queries) if valid_queries else "(제공 안 함 - 기사 내용에만 집중할 것)"
        blocks.append(f"### 키워드: {e['keyword']}\n[수집된 실제 뉴스 기사 ({len(arts)}건)]\n{mat}\n[참고용 연관 검색어]\n{q_str}")

    prompt = (
        f"너는 수석 경제 뉴스레터 에디터야. {period_text(start, end)} 기간에 실제 보도된 아래 뉴스 기사들을 바탕으로 키워드별 뉴스레터 꼭지를 작성해.\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        "[작성 원칙 및 절대 주의사항 - 위반 시 불합격]\n"
        "1. 오직 [수집된 실제 뉴스 기사]에 등장하는 구체적인 사건, 수치, 발표 내용, 시장 반응만 바탕으로 작성할 것.\n"
        "2. 절대 금지 (언론사 인용구 및 출처 표기 금지): 본문(facts)에 'OO뉴스에 따르면', 'OO 보도에 의하면', 'OO는 ~라고 전했다', '~라고 보도했다', '~라고 덧붙였다' 같은 언론사 이름이나 전달형 서술어를 절대 쓰지 말 것! "
        "출처 링크는 하단에 별도로 제공되므로, 문장 처음부터 곧바로 핵심 사실(주체·수치·원인·결과)만 직관적으로 서술할 것.\n"
        "   - 나쁜 예: '연합뉴스 보도에 따르면 나스닥 지수가 5.5% 폭락했다고 전했다.'\n"
        "   - 좋은 예: '뉴욕증시에서 나스닥 지수가 AI 기술주 투매 여파로 5.5% 폭락하며 장중 조정장에 진입했다.'\n"
        "3. 절대 금지 (교과서 상식·개념 설명 금지): '코스피란 한국거래소에 상장된~', '기준금리는 중앙은행이~', '부동산114나 네이버 부동산에서~', "
        "'전자계약시스템은~', '뉴욕증시 개장시간과 서머타임은~' 같은 사전적 용어 정의나 포털/플랫폼 소개, 거래 시간 안내 등 일반 상식을 절대 쓰지 말 것.\n"
        "4. 절대 금지 (메타 발언 금지): '수집된 기사가 없어 확인이 필요하다', '데이터가 존재하지 않는다', '직접적인 확인이 필요하다' 같은 문장을 절대 쓰지 말 것.\n"
        "5. 문체: 본문(facts)과 Q&A 답변(a)은 간결하고 명확한 평서문('~이다', '~했다', '~전망된다', '~나타났다')으로 통일하고, "
        "모든 Q&A 질문(q)의 끝맺음은 반드시 '~인가?' 또는 '~는가?' 문체로만 통일할 것 ('~입니까?', '~인가요?', '~할까요?', '~입니다', '~합니다', '~습니다' 절대 금지, 한자 표기 금지).\n\n"
        "[키워드별 JSON 작성 항목]\n"
        "- headline: 이번 뉴스 사건의 핵심을 찌르는 뉴스레터 제목 (25자 이내, 과장 금지, '기본 개념/정의/안내' 같은 상식형 제목 절대 금지)\n"
        "- facts: '무슨 일이 있었나'를 정리한 불릿 4~5개 (문자열 배열). 언론사 언급 없이 핵심 사건·구체적 수치·배경·결과만 80~140자 분량으로 깔끔하게 작성.\n"
        "- watch: 이번 이슈와 관련해 앞으로 시장에서 지켜볼 후속 일정·지표·체크포인트 2~3개 (문자열 배열, 각 45자 이내)\n"
        "- qna: 독자에게 깊은 인사이트를 주는 고품질 심층 Q&A 3개 (각 답변 2~3문장).\n"
        "  [Q&A 고품질 작성 지침 - 매우 중요]\n"
        "  * 기계적이고 뻔한 포괄적 질문('이유는 무엇인가?', '개인 자산에 어떤 영향을 미치는가?', '유의할 점은 무엇인가?')을 모든 카드에 똑같이 복붙하듯 쓰지 말 것!\n"
        "  * 이미 facts에서 요약한 내용을 단순 반복해 묻지 말고, 기사에 등장한 구체적인 정책·수치·역설적 현상·이해관계를 콕 집어 질문할 것:\n"
        "    - Q1 (핵심 쟁점·역설·메커니즘 해부): 기사 속 특정 조치나 수치, 혹은 언뜻 이해하기 어려운 시장의 역설적 반응(예: '고용이 늘었는데 왜 금리 인상 공포가 커지는가?', '신용융자 담보비율 140% 면제 조치가 어떻게 반대매매 폭탄을 막는가?', '나스닥 급락 속에서도 왜 레버리지 ETF에 3조 원이 몰렸는가?')을 구체적으로 짚어 작동 원리를 설명.\n"
        "    - Q2 (연쇄 파급 경로와 이해득실): 이 사건이 관련 산업·기업 밸류체인·환율·금리·가계/투자자 포지션에 어떤 구체적인 경로로 파급되는지(누가 수혜를 입고 어디서 리스크가 터지는지) 분석.\n"
        "    - Q3 (시장 반전·지속의 분기점): 전문가나 시장에서 엇갈리는 시각을 짚거나, 향후 추세가 바뀔 수 있는 구체적인 분기점(특정 지표·수치·법안·실적 조건)이 무엇인지 제시.\n"
        "  * 답변 작성 시 '일희일비하지 말고', '신중하게 접근할 필요가 있다', '다각도로 점검해야 한다' 같은 공허한 교과서식 훈계 문장은 절대 쓰지 말고, 구체적 근거와 메커니즘으로만 답할 것.\n"
        "  * 모든 질문(q)은 반드시 '~인가?' 또는 '~는가?'로 끝낼 것.\n\n"
        "반드시 아래 형식의 JSON 객체로만 답해 (최상위 키는 입력된 키워드 문자열과 100% 동일해야 함):\n"
        '{"<키워드>": {"headline": "...", "facts": ["..."], "watch": ["..."], '
        '"qna": [{"q": "...", "a": "..."}, {"q": "...", "a": "..."}, {"q": "...", "a": "..."}]}}'
    )

    try:
        raw = _extract_json(_gemini(prompt, api_key))
    except json.JSONDecodeError:
        raw = _extract_json(_gemini(prompt, api_key))

    out = {}
    for e in entries:
        kw = e["keyword"]
        d = raw.get(kw)
        if not isinstance(d, dict):
            out[kw] = {"error": "Gemini 응답에 이 키워드 결과가 없습니다."}
            continue
        qna_list = []
        for qa in d.get("qna", [])[:3]:
            if isinstance(qa, dict):
                qna_list.append({
                    "q": _normalize_question_tone(qa.get("q", "")),
                    "a": _normalize_declarative_tone(qa.get("a", "")),
                })
        d["qna"] = qna_list
        for k in ("facts", "watch"):
            v = d.get(k, [])
            items = v if isinstance(v, list) else [str(v)]
            if k == "facts":
                items = [
                    _normalize_declarative_tone(_strip_media_attribution(str(x)))
                    for x in items
                    if str(x).strip()
                ]
            d[k] = items
        out[kw] = d

    if all("error" not in v for v in out.values()):
        cache_put(cache_key, out)
    return out


def _format_item_md(idx: int, kw: str, d: dict, arts: list) -> list:
    lines = ["---", "", f"### {idx}. {d.get('headline') or kw}", f"> 키워드: {kw}", "", "**📌 무슨 일이 있었나**"]
    lines += [f"- {f}" for f in d.get("facts", [])]
    lines += ["", "**💬 핵심 Q&A**"]
    for qa in d.get("qna", []):
        lines += [f"- **Q. {qa['q']}**", f"  A. {qa['a']}"]
    if d.get("watch"):
        lines += ["", "**👀 앞으로 볼 포인트**"] + [f"- {w}" for w in d["watch"]]
    if arts:
        lines += ["", "<sub>참고: " + " / ".join(f"[{a.get('source') or '기사'}]({a.get('link') or '#'})" for a in arts[:3]) + "</sub>"]
    lines.append("")
    return lines


def build_newsletter_md(
    domestic_items: list,
    global_items: list = None,
    start: datetime = None,
    end: datetime = None,
    today: datetime = None,
) -> str:
    """domestic_items / global_items: [(keyword, data, articles)] -> 뉴스레터 마크다운 (복사/다운로드용)"""
    today = today or datetime.now()
    start = start or today
    end = end or today

    if global_items is None:
        items = domestic_items
        md = [f"# ⚡ ZIPPY | AI 경제 데일리 브리핑", f"_{period_text(start, end)} 이슈 · 발행 {today:%Y-%m-%d}_", ""]
        md.append("**주요 키워드:** " + " · ".join(f"`{k}`" for k, d, _ in items if "error" not in d))
        md.append("")
        for n, (kw, d, arts) in enumerate(items, 1):
            if "error" in d:
                continue
            md += _format_item_md(n, kw, d, arts)
        md += ["---", "_본 뉴스레터는 AI가 공개 뉴스 보도를 바탕으로 작성했으며 특정 자산에 대한 투자 권유가 아닙니다._"]
        return "\n".join(md)

    dom_kws = [k for k, d, _ in domestic_items if "error" not in d]
    gl_kws = [k for k, d, _ in global_items if "error" not in d]

    md = [
        "# ⚡ ZIPPY | AI 경제 데일리 브리핑",
        f"_{period_text(start, end)} 이슈 · 발행 {today:%Y-%m-%d}_",
        "",
        f"🇰🇷 **국내 경제:** " + (" · ".join(f"`{k}`" for k in dom_kws) or "없음"),
        f"🌐 **해외 경제:** " + (" · ".join(f"`{k}`" for k in gl_kws) or "없음"),
        "",
        "## 🇰🇷 국내 경제 핵심 이슈",
        "",
    ]
    for n, (kw, d, arts) in enumerate(domestic_items, 1):
        if "error" in d:
            continue
        md += _format_item_md(n, kw, d, arts)

    md += [
        "",
        "## 🌐 해외·글로벌 경제 핵심 이슈",
        "",
    ]
    for n, (kw, d, arts) in enumerate(global_items, 1):
        if "error" in d:
            continue
        md += _format_item_md(n, kw, d, arts)

    md += ["---", "_본 뉴스레터는 AI가 공개 뉴스 보도를 바탕으로 작성했으며 특정 자산에 대한 투자 권유가 아닙니다._"]
    return "\n".join(md)
