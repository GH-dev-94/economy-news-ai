"""경제 뉴스 AI 파이프라인 (실제 구현)
  [Step 1~2] 어제 기준 경제 키워드 추출 (Google Trends RSS + 경제 뉴스 헤드라인 + Gemini)
  [Step 3]   키워드별 최신 뉴스 수집 (Google News RSS)
  [Step 4~5] Gemini로 대중 관심사 분석 + 이슈 요약 + Q&A 3개 생성
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
TIMEOUT = 10

ECON_WORDS = [
    "금리", "환율", "주가", "증시", "코스피", "코스닥", "삼성전자", "배당", "세금", "금투세", "부동산", "물가",
    "인플레", "경제", "은행", "대출", "투자", "주식", "반도체", "달러", "연준", "fomc", "etf", "비트코인",
    "코인", "유가", "수출", "무역", "관세", "적자", "흑자", "gdp", "채권", "아파트", "전세", "청약", "연금",
    "세제", "예금", "펀드", "나스닥", "엔비디아", "테슬라", "실적", "상장", "ipo", "예산", "추경", "지수",
]

GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]

# 국가별 설정
COUNTRY = {
    "KR": {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},
    "US": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}


MAX_SPAN_DAYS = 31      # 구글 뉴스 RSS는 쿼리당 최대 100건 -> 기간이 길면 대표성이 떨어져 31일로 제한
MAX_LOOKBACK_DAYS = 365  # 오래된 날짜는 기사 수가 급감(예: 1년 전 하루치 약 9건)


def period_text(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%Y-%m-%d}"
    return f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"


def _is_econ(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ECON_WORDS)


def _parse_rss(url: str):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return ET.fromstring(r.content)


# -----------------------------------------------------------------------------
# [Step 1~2] 경제 헤드라인 수집 및 키워드 선별
# -----------------------------------------------------------------------------
ECON_TOPICS = {"KR": ["경제", "금리 환율", "증시 코스피", "부동산 아파트", "물가 소비", "반도체 수출", "세금 연금"],
               "US": ["economy markets", "fed rates inflation", "stock market", "housing market", "jobs report"]}


def fetch_econ_headlines(country: str = "KR", start: datetime = None, end: datetime = None, limit: int = 60):
    """Google News 경제 토픽별 헤드라인 (기간 내). 토픽을 나눠 다양한 이슈를 확보."""
    c = COUNTRY[country]
    start = start or (datetime.now() - timedelta(days=1)); end = end or datetime.now()
    out, seen = [], set()
    for topic in ECON_TOPICS[country]:
        q = topic + f" after:{start - timedelta(days=1):%Y-%m-%d} before:{end + timedelta(days=1):%Y-%m-%d}"
        url = (f"https://news.google.com/rss/search?q={quote(q)}"
               f"&hl={c['hl']}&gl={c['gl']}&ceid={c['ceid']}")
        try:
            root = _parse_rss(url)
        except Exception:
            continue
        n = 0
        for i in root.iter("item"):
            try:
                if not (start.date() <= parsedate_to_datetime(i.findtext("pubDate") or "").astimezone().date() <= end.date()):
                    continue
            except Exception:
                pass
            t = (i.findtext("title") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
                n += 1
            if n >= 12:
                break
    return out[:limit]


def _gemini(prompt: str, api_key: str, model_override: str = None) -> str:
    """Gemini REST API 직접 호출 (gRPC 'Illegal header value' 문제 회피, 지원종료된 SDK 불필요)."""
    import os
    base = "https://generativelanguage.googleapis.com/v1beta"
    hdr = {"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"}
    names = [model_override or os.getenv("GEMINI_MODEL", "")] + GEMINI_MODELS
    try:  # 계정에서 실제 사용 가능한 flash 모델을 동적으로 추가
        r = requests.get(f"{base}/models?pageSize=200", headers=hdr, timeout=TIMEOUT)
        r.raise_for_status()
        avail = [m["name"].replace("models/", "") for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]
                 and not any(x in m["name"] for x in ("lite", "preview", "tts", "image", "omni", "latest"))]
        names += sorted(avail, reverse=True)
    except Exception:
        pass
    errors, seen = [], set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            r = requests.post(f"{base}/models/{name}:generateContent", headers=hdr, timeout=40,
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"responseMimeType": "application/json",
                                                         "maxOutputTokens": 16384}})
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


_STOP = {"경제", "뉴스", "오늘", "속보", "종합", "단독", "포토", "영상", "기자", "전망", "분석", "이번", "올해", "내년",
         "대한", "관련", "위해", "통해", "에서", "으로", "한국", "정부", "시장", "업계", "기업"}
_JOSA = ("에서", "으로", "에게", "까지", "부터", "이다", "은", "는", "이", "가", "을", "를", "의", "에", "도", "로", "와", "과", "만")




def _top_econ_terms(headlines: list, topn: int = 10):
    """헤드라인에서 경제 용어를 포함한 단어를 빈도순으로 추출 (조사 간단 제거)."""
    from collections import Counter
    cnt = Counter()
    for h in headlines:
        title = re.sub(r"\s+-\s+[^-]+$", "", h)  # 언론사 꼬리 제거
        seen = set()
        for tok in re.findall(r"[0-9A-Za-z가-힣%.]+", title):
            for j in _JOSA:
                if len(tok) > len(j) + 1 and tok.endswith(j):
                    tok = tok[: -len(j)]
                    break
            if len(tok) < 2 or tok in _STOP or "경제" in tok or tok.isdigit() or tok in seen:
                continue
            seen.add(tok)
            cnt[tok] += 1
    econ = [(w, c) for w, c in cnt.items() if _is_econ(w)]
    econ.sort(key=lambda x: (-x[1], -len(x[0])))
    return [w for w, _ in econ[:topn]]


def extract_keywords(n: int, country: str, start: datetime, end: datetime, api_key: str = ""):
    """선택 기간의 경제 뉴스 헤드라인(증시, 환율, 금리, 부동산, 산업 등)을 분석하여 핵심 키워드 n개 선별.
    returns (keywords: list[str], info: dict)
    """
    ck = ("kw", n, country, start.date(), end.date())
    if api_key and cache_get(ck):
        kws_c, info_c = cache_get(ck)
        return kws_c, {**info_c, "cached": True}

    headlines = fetch_econ_headlines(country, start, end)
    info = {"headlines_count": len(headlines), "method": ""}

    if api_key and headlines:
        prompt = (
            f"{period_text(start, end)} 기간의 주요 분야별 경제 뉴스 헤드라인 목록이야.\n"
            f"[경제 뉴스 헤드라인 ({len(headlines)}건)]\n" + "\n".join(f"- {h}" for h in headlines) + "\n\n"
            f"위 뉴스들을 종합 분석하여, 현재 가장 중요하고 대중 관심과 시장 영향력이 큰 핵심 경제 키워드 {n}개를 선별해줘.\n"
            f"JSON 문자열 배열로만 답해. 각 키워드는 2~12자의 명확하고 검색 가능한 경제 구절이어야 해. "
            f"(예: '기준금리 인하', '금투세 폐지', '원달러 환율', '삼성전자 실적' 등)"
        )
        try:
            kws = _extract_json(_gemini(prompt, api_key))
            kws = [str(k).strip() for k in kws if str(k).strip()][:n]
            if kws:
                info["mode"] = "gemini"
                info["method"] = "Gemini AI가 경제 헤드라인 종합 분석 후 선별"
                cache_put(ck, (kws, info))
                return kws, info
        except Exception as e:
            info["gemini_error"] = str(e)
            info["fallback_reason"] = "quota" if "429" in str(e) else "error"

    # 폴백(Gemini 불가 시): 헤드라인 속 경제 핵심어 빈도수 기반 자동 추출
    kws = _top_econ_terms(headlines, topn=n)
    info["mode"] = "fallback"
    info.setdefault("fallback_reason", "nokey" if not api_key else "nodata")
    info["method"] = "규칙 기반(경제 헤드라인 빈출 핵심어)"
    return kws[:n], info


# -----------------------------------------------------------------------------
# [Step 3] 뉴스 수집
# -----------------------------------------------------------------------------
def fetch_news(keyword: str, country: str, start: datetime, end: datetime, limit: int = 8):
    c = COUNTRY[country]
    after = (start - timedelta(days=1)).strftime("%Y-%m-%d")  # after/before는 경계가 모호해 넓게 요청 후 직접 필터
    before = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    q = f"{keyword} after:{after} before:{before}"
    url = (f"https://news.google.com/rss/search?q={quote(q)}"
           f"&hl={c['hl']}&gl={c['gl']}&ceid={c['ceid']}")
    articles = []
    try:
        root = _parse_rss(url)
        for item in root.iter("item"):
            desc = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)
            pub = item.findtext("pubDate") or ""
            try:
                if not (start.date() <= parsedate_to_datetime(pub).astimezone().date() <= end.date()):
                    continue  # 선택 기간 밖 기사 제외 (로컬 시간 기준)
            except Exception:
                pass
            try:
                pub = parsedate_to_datetime(pub).astimezone().strftime("%m-%d %H:%M")
            except Exception:
                pass
            articles.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "source": (item.findtext("source") or "").strip(),
                "published": pub,
                "snippet": desc,
            })
            if len(articles) >= limit:
                break
    except Exception:
        pass
    return articles


# -----------------------------------------------------------------------------
# [Step 4~5] AI 분석 + 원고 생성
# -----------------------------------------------------------------------------
QUESTION_SUFFIXES = ["", " 뜻", " 영향"]


def fetch_search_queries(keyword: str, country: str = "KR", max_items: int = 8):
    """구글 검색 자동완성(비공식)으로 사람들이 실제 검색하는 연관 검색어 수집. 실패 시 빈 리스트."""
    hl = COUNTRY[country]["hl"]
    suffixes = QUESTION_SUFFIXES if country == "KR" else ["", " meaning", " impact"]
    seen, out = {keyword}, []
    for suf in suffixes:
        try:
            r = requests.get("https://suggestqueries.google.com/complete/search",
                             params={"client": "firefox", "q": keyword + suf, "hl": hl},
                             headers=HEADERS, timeout=2.5)
            r.raise_for_status()
            for q in r.json()[1]:
                q = q.strip()
                if q and q not in seen:
                    seen.add(q)
                    out.append(q)
        except Exception:
            continue
    return out[:max_items]


def generate_manuscript(keyword: str, articles: list, start: datetime, end: datetime, api_key: str,
                        queries: list = None):
    """뉴스레터 한 꼭지 생성.
    returns {"headline": str, "facts": [str], "why": str, "watch": [str], "qna": [{"q","a"}x3]}
    - facts(사실)와 qna(의미·영향·행동)의 역할을 분리해 내용 중복을 막는다.
    """
    material = "\n".join(f"- [{a['source']}] {a['title']}" for a in articles) or "(수집된 기사 없음)"
    prompt = (
        f"너는 경제 뉴스레터 에디터야. {period_text(start, end)} 기간의 뉴스를 바탕으로 키워드 '{keyword}' 꼭지를 써.\n"
        f"[수집된 기사 제목]\n{material}\n\n"
        + (f"[사람들이 실제로 구글에 검색하는 연관 검색어]\n{', '.join(queries)}\n\n" if queries else "")
        + "독자는 경제 뉴스를 잘 모르는 일반인이고, 바쁘니까 짧고 쉽게 써.\n"
        "아래 항목을 JSON으로 작성:\n"
        "- headline: 이 꼭지의 뉴스레터 제목(25자 이내, 호기심을 끌되 과장 금지)\n"
        "- facts: '무슨 일이 있었나'만 담은 불릿 3~5개(문자열 배열, 각 60자 이내, 기사에 있는 사실만)\n"
        "- why: '그래서 왜 중요한가/나에게 어떤 영향인가'를 쉬운 말로 2문장 이내\n"
        "- watch: 앞으로 지켜볼 포인트(일정·변수) 2~3개(문자열 배열, 각 50자 이내)\n"
        "- qna: 독자가 실제로 궁금해할 질문 3개와 답. 위 연관 검색어가 있으면 그중 가장 대표적인 검색 의도를 골라 자연스러운 질문 문장으로 바꿔 쓸 것(연관 검색어에 없는 질문은 최소화). 단, facts에 이미 쓴 내용을 다시 묻거나 반복하면 안 되고, "
        "'내 돈·생활에 미치는 영향', '용어·배경 쉬운 설명', '그래서 어떻게 해야 하나(관점, 단정적 투자권유 금지)' 중에서만 고를 것. "
        "각 답변은 2~3문장.\n"
        "기사에 없는 수치·날짜는 지어내지 말고, 모르면 '확인 필요'라고 써. 반드시 아래 JSON 형식으로만 답해:\n"
        '{"headline": "...", "facts": ["..."], "why": "...", "watch": ["..."], '
        '"qna": [{"q": "...", "a": "..."}, {"q": "...", "a": "..."}, {"q": "...", "a": "..."}]}'
    )
    data = _extract_json(_gemini(prompt, api_key))
    data["qna"] = data.get("qna", [])[:3]
    for k in ("facts", "watch"):
        v = data.get(k, [])
        data[k] = v if isinstance(v, list) else [str(v)]
    return data


_CACHE = {}
CACHE_TTL = 30 * 60  # 30분


def cache_get(key):
    v = _CACHE.get(key)
    if v and (datetime.now().timestamp() - v[0]) < CACHE_TTL:
        return v[1]
    return None


def cache_put(key, value):
    _CACHE[key] = (datetime.now().timestamp(), value)


def generate_manuscripts_batch(entries: list, start: datetime, end: datetime, api_key: str):
    """키워드 여러 개를 Gemini 1회 호출로 한 번에 생성 (무료 티어 일일 20회 제한 절약).
    entries: [{"keyword", "articles", "queries"}]  returns {keyword: data}
    """
    cache_key = ("ms", tuple(e["keyword"] for e in entries), start.date(), end.date())
    hit = cache_get(cache_key)
    if hit:
        return hit
    blocks = []
    for e in entries:
        mat = "\n".join(f"- [{a['source']}] {a['title']}" for a in e["articles"]) or "(수집된 기사 없음)"
        q = ", ".join(e.get("queries") or []) or "(없음)"
        blocks.append(f"### 키워드: {e['keyword']}\n[기사 제목]\n{mat}\n[실제 구글 연관 검색어]\n{q}")
    prompt = (
        f"너는 경제 뉴스레터 에디터야. {period_text(start, end)} 기간의 뉴스를 바탕으로, 아래 키워드별로 뉴스레터 꼭지를 써.\n\n"
        + "\n\n".join(blocks) + "\n\n"
        "독자는 경제 뉴스를 잘 모르는 일반인이고, 바쁘니까 짧고 쉽게 써. 키워드마다 아래 항목을 작성:\n"
        "- headline: 뉴스레터 제목(25자 이내, 호기심을 끌되 과장 금지)\n"
        "- facts: '무슨 일이 있었나'만 담은 불릿 3~5개(문자열 배열, 각 60자 이내, 기사에 있는 사실만)\n"
        "- why: '왜 중요한가/나에게 어떤 영향인가' 쉬운 말로 2문장 이내\n"
        "- watch: 앞으로 지켜볼 포인트 2~3개(문자열 배열, 각 50자 이내)\n"
        "- qna: 독자 질문 3개와 답. 연관 검색어가 있으면 그 검색 의도를 자연스러운 질문 문장으로 바꿔 쓸 것. "
        "facts 내용을 반복해서 묻지 말고 '내 돈·생활 영향', '용어·배경 설명', '어떻게 봐야 하나(단정적 투자권유 금지)' 중에서만. 답변은 2~3문장.\n"
        "기사에 없는 수치·날짜는 지어내지 말고 모르면 '확인 필요'. 반드시 아래 형식의 JSON 객체로만 답해(키는 키워드 문자열 그대로):\n"
        '{"<키워드>": {"headline": "...", "facts": ["..."], "why": "...", "watch": ["..."], '
        '"qna": [{"q": "...", "a": "..."}, {"q": "...", "a": "..."}, {"q": "...", "a": "..."}]}}'
    )
    try:
        raw = _extract_json(_gemini(prompt, api_key))
        raw = _extract_json(_gemini(prompt, api_key))
    out = {}
    for e in entries:
        d = raw.get(e["keyword"])
        if not isinstance(d, dict):
            out[e["keyword"]] = {"error": "Gemini 응답에 이 키워드 결과가 없습니다."}
            continue
        d["qna"] = d.get("qna", [])[:3]
        for k in ("facts", "watch"):
            v = d.get(k, [])
            d[k] = v if isinstance(v, list) else [str(v)]
        out[e["keyword"]] = d
    if all("error" not in v for v in out.values()):
        cache_put(cache_key, out)
    return out


def build_newsletter_md(items: list, start: datetime, end: datetime, today: datetime) -> str:
    """items: [(keyword, data, articles)] -> 뉴스레터 마크다운 (복사/다운로드용)"""
    md = [f"# 📈 오늘의 경제 뉴스레터", f"_{period_text(start, end)} 이슈 · 발행 {today:%Y-%m-%d}_", ""]
    md.append("**오늘의 키워드:** " + " · ".join(f"`{k}`" for k, d, _ in items if "error" not in d))
    md.append("")
    for n, (kw, d, arts) in enumerate(items, 1):
        if "error" in d:
            continue
        md += ["---", "", f"## {n}. {d.get('headline') or kw}", f"> 키워드: {kw}", "", "**📌 무슨 일이 있었나**"]
        md += [f"- {f}" for f in d["facts"]]
        md += ["", "**🧭 왜 중요할까**", d.get("why", ""), "", "**💬 독자들이 궁금해하는 것**"]
        for qa in d["qna"]:
            md += [f"- **Q. {qa['q']}**", f"  A. {qa['a']}"]
        if d["watch"]:
            md += ["", "**👀 앞으로 볼 포인트**"] + [f"- {w}" for w in d["watch"]]
        if arts:
            md += ["", "<sub>참고: " + " / ".join(f"[{a['source'] or '기사'}]({a['link']})" for a in arts[:3]) + "</sub>"]
        md.append("")
    md += ["---", "_본 뉴스레터는 AI가 공개 뉴스 제목을 바탕으로 작성했으며 투자 권유가 아닙니다._"]
    return "\n".join(md)
