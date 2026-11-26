"""ZIPPY 정적 멀티페이지(아카이브 · 경제 가이드 · 메인 프리렌더링 · 사이트맵) 빌더
- 일자별 경제 브리핑 아카이브(/archive/YYYY-MM-DD.html, /archive/index.html) 생성 및 Git 히스토리 백필
- 상설 경제·투자 심층 가이드(/guide/index.html 및 8편의 해설 아티클) 생성
- 메인 페이지(index.html) 국내·해외 전체 카드 펼침(open) 프리렌더링 동기화
- 전체 정적 URL을 포함하는 sitemap.xml 자동 생성
"""
import html as _html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
GUIDE_DIR = os.path.join(BASE_DIR, "guide")


COMMON_CSS = """
    :root {
      --bg: #0e1117;
      --bg-card: #161a23;
      --bg-card-hover: #1c222e;
      --text-main: #fafafa;
      --text-muted: #94a3b8;
      --border: rgba(250, 250, 250, 0.14);
      --accent-blue: #3b82f6;
      --accent-red: #ff4b4b;
      --neon-green: #3dd56d;
      --neon-green-bg: rgba(61, 213, 109, 0.14);
      --quote-bg: rgba(255, 255, 255, 0.04);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 0;
      background-color: var(--bg);
      color: var(--text-main);
      font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      line-height: 1.72;
      -webkit-font-smoothing: antialiased;
      word-break: keep-all;
      overflow-wrap: break-word;
    }
    .container { max-width: 980px; margin: 0 auto; padding: 1.4rem 1.25rem 3.5rem; }
    .top-nav {
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 0.75rem;
      padding-bottom: 0.9rem; margin-bottom: 1.25rem;
      border-bottom: 2px solid var(--accent-blue);
    }
    .brand-link {
      text-decoration: none; color: var(--text-main);
      font-size: 1.35rem; font-weight: 800; letter-spacing: -0.02em;
      white-space: nowrap;
    }
    .nav-links { display: flex; flex-wrap: wrap; gap: 0.42rem; }
    .nav-links a {
      text-decoration: none; color: var(--text-muted);
      font-size: 0.86rem; font-weight: 600;
      padding: 0.38rem 0.72rem; border-radius: 7px;
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.03);
      transition: all 0.15s ease;
      white-space: nowrap;
    }
    .nav-links a:hover, .nav-links a.active {
      color: var(--text-main);
      background: rgba(59, 130, 246, 0.16);
      border-color: var(--accent-blue);
    }
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem 1.45rem;
      margin-bottom: 1.15rem;
    }
    .badge {
      display: inline-block; background: var(--neon-green-bg); color: var(--neon-green);
      font-size: 0.78rem; font-weight: 700; padding: 0.15rem 0.55rem; border-radius: 5px; margin-bottom: 0.4rem;
    }
    .kw-badge {
      display: inline-block; background: var(--neon-green-bg); color: var(--neon-green);
      font-size: 0.8rem; font-weight: 600; padding: 0.18rem 0.58rem; border-radius: 5px;
      margin-right: 0.35rem; margin-bottom: 0.25rem; text-decoration: none;
    }
    h1 { font-size: 1.6rem; margin: 0 0 0.55rem; line-height: 1.35; }
    h2 { font-size: 1.22rem; margin: 1.55rem 0 0.65rem; color: #60a5fa; border-left: 3px solid var(--accent-blue); padding-left: 0.65rem; }
    h3 { font-size: 1.06rem; margin: 1.15rem 0 0.45rem; color: var(--text-main); }
    h4 { font-size: 0.98rem; margin: 0.95rem 0 0.4rem; color: #93c5fd; }
    p, li { color: #e2e8f0; font-size: 0.95rem; }
    ul, ol { padding-left: 1.3rem; margin: 0.45rem 0 0.85rem; }
    li { margin-bottom: 0.4rem; }
    .qna-item { margin-bottom: 0.95rem; }
    .qna-q { font-weight: 700; color: var(--text-main); margin-bottom: 0.32rem; font-size: 0.95rem; }
    .qna-a {
      margin: 0; padding: 0.65rem 0.95rem;
      background: var(--quote-bg); border-left: 3px solid var(--accent-blue);
      border-radius: 4px; color: #cbd5e1; font-size: 0.92rem;
    }
    .table-wrap { overflow-x: auto; margin: 0.85rem 0; }
    table.info-table { width: 100%; border-collapse: collapse; font-size: 0.87rem; }
    table.info-table th, table.info-table td {
      padding: 0.62rem 0.65rem; border: 1px solid var(--border); text-align: left;
    }
    table.info-table th { background: rgba(255, 255, 255, 0.04); color: #93c5fd; font-weight: 700; }
    .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
    .link-card {
      display: block; text-decoration: none; color: inherit;
      background: var(--bg-card); border: 1px solid var(--border);
      border-radius: 11px; padding: 1.2rem 1.25rem; transition: all 0.15s;
    }
    .link-card:hover { border-color: var(--accent-blue); background: var(--bg-card-hover); transform: translateY(-1px); }
    .footer {
      margin-top: 2.5rem; padding-top: 1.3rem;
      border-top: 1px solid var(--border);
      color: var(--text-muted); font-size: 0.82rem; line-height: 1.65; text-align: center;
    }
    .footer-links {
      display: flex; justify-content: center; flex-wrap: wrap; gap: 1.1rem; margin-bottom: 0.65rem;
    }
    .footer-links a { color: var(--text-muted); text-decoration: none; font-size: 0.84rem; }
    .footer-links a:hover { color: var(--text-main); text-decoration: underline; }
    @media (max-width: 640px) {
      .container { padding: 0.95rem 0.85rem 2.8rem; }
      .top-nav { gap: 0.55rem; padding-bottom: 0.7rem; margin-bottom: 0.95rem; }
      .brand-link { font-size: 1.22rem; }
      .nav-links {
        flex-wrap: nowrap;
        overflow-x: auto;
        width: 100%;
        gap: 0.35rem;
        padding-bottom: 0.15rem;
        scrollbar-width: none;
        -webkit-overflow-scrolling: touch;
      }
      .nav-links::-webkit-scrollbar { display: none; }
      .nav-links a { font-size: 0.8rem; padding: 0.34rem 0.62rem; flex-shrink: 0; }
      .card { padding: 1.05rem 0.95rem; margin-bottom: 0.9rem; }
      h1 { font-size: 1.32rem; line-height: 1.32; }
      h2 { font-size: 1.08rem; margin: 1.25rem 0 0.55rem; }
      h3 { font-size: 0.98rem; }
      p, li { font-size: 0.9rem; line-height: 1.65; }
      ul, ol { padding-left: 1.1rem; }
      .qna-q { font-size: 0.9rem; }
      .qna-a { padding: 0.55rem 0.75rem; font-size: 0.87rem; }
      .kw-badge { font-size: 0.76rem; padding: 0.14rem 0.45rem; white-space: nowrap; }
      table.info-table { font-size: 0.8rem; }
      table.info-table th, table.info-table td { padding: 0.48rem 0.5rem; }
    }
"""


def _render_layout(
    title: str,
    description: str,
    canonical_path: str,
    active_nav: str,
    body_html: str,
    rel_root: str = "..",
    json_ld: dict | None = None,
) -> str:
    canonical_url = f"https://zippy.kr/{canonical_path.lstrip('/')}"
    ld_script = ""
    if json_ld:
        ld_script = f'<script type="application/ld+json">\n{json.dumps(json_ld, ensure_ascii=False, indent=2)}\n</script>'

    def nav_cls(key: str) -> str:
        return ' class="active"' if active_nav == key else ""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0" />
  <title>{_html.escape(title)}</title>
  <meta name="description" content="{_html.escape(description)}" />
  <link rel="canonical" href="{canonical_url}" />
  <meta property="og:title" content="{_html.escape(title)}" />
  <meta property="og:description" content="{_html.escape(description)}" />
  <meta property="og:url" content="{canonical_url}" />
  <meta property="og:type" content="article" />
  <meta name="theme-color" content="#0e1117" />
  <link rel="manifest" href="{rel_root}/manifest.json" />
  <link rel="apple-touch-icon" href="{rel_root}/icons/icon-192.png" />
  <link rel="icon" type="image/png" sizes="192x192" href="{rel_root}/icons/icon-192.png" />
  <link rel="stylesheet" as="style" crossorigin href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css" />
  <meta name="google-adsense-account" content="ca-pub-8243997410078269" />
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8243997410078269" crossorigin="anonymous"></script>
  {ld_script}
  <style>{COMMON_CSS}</style>
</head>
<body>
  <div class="container">
    <header class="top-nav">
      <a href="{rel_root}/index.html" class="brand-link">⚡ ZIPPY</a>
      <nav class="nav-links" aria-label="주요 메뉴">
        <a href="{rel_root}/index.html"{nav_cls("home")}>⚡ 경제 브리핑</a>
        <a href="{rel_root}/archive/index.html"{nav_cls("archive")}>🗂️ 브리핑 아카이브</a>
        <a href="{rel_root}/guide/index.html"{nav_cls("guide")}>📘 경제·투자 가이드</a>
        <a href="{rel_root}/tools/dsr.html"{nav_cls("tools")}>🧮 금융 계산기</a>
        <a href="{rel_root}/about.html"{nav_cls("about")}>ℹ️ 서비스 소개</a>
      </nav>
    </header>

    {body_html}

    <footer class="footer">
      <div class="footer-links">
        <a href="{rel_root}/index.html">경제 브리핑</a>
        <a href="{rel_root}/archive/index.html">브리핑 아카이브</a>
        <a href="{rel_root}/guide/index.html">경제·투자 가이드</a>
        <a href="{rel_root}/tools/dsr.html">금융 계산기</a>
        <a href="{rel_root}/about.html">서비스 소개</a>
        <a href="{rel_root}/privacy.html">개인정보처리방침</a>
        <a href="{rel_root}/terms.html">이용약관 및 면책고지</a>
        <a href="{rel_root}/contact.html">문의 및 정정요청</a>
      </div>
      <div>
        ※ 본 서비스의 모든 브리핑 및 해설은 정보 제공 목적의 참고 자료이며, 특정 금융 상품이나 종목에 대한 투자 권유가 아닙니다.<br />
        © ZIPPY Economy Briefing (zippy.kr). All rights reserved.
      </div>
    </footer>
  </div>
</body>
</html>
"""


# =====================================================================
# 1. 에버그린 경제·투자 가이드 8편 정의 및 생성
# =====================================================================
GUIDES_DATA = [
    {
        "slug": "stress-dsr-complete-guide",
        "badge": "대출·부동산 규제",
        "title": "2026 스트레스 DSR 3단계 완벽 가이드: 수도권 3.0% vs 지방 0.75%, 금리유형별 한도 차이",
        "desc": "스트레스 DSR 3단계 시행에 따른 수도권(하한 3.0%p)과 지방(0.75%p 유예) 차등 기준, 고정·주기형·혼합형·변동금리 가중치(0%~100%)별 주택담보대출 한도 차이를 실제 수치로 해설합니다.",
        "body": """
      <p>주택 구입이나 갈아타기를 준비하는 실수요자에게 현재 대출 한도를 결정짓는 가장 결정적인 변수는 LTV(담보인정비율)가 아니라 <strong>스트레스 DSR(총부채원리금상환비율)</strong>입니다. 같은 연봉을 받더라도 <strong>주택 소재지(수도권 vs 지방)</strong>와 <strong>금리 유형(순수고정·주기형·혼합형·변동금리)</strong>을 어떻게 선택하느냐에 따라 대출 가능 금액이 수천만 원에서 1억 원 이상 차이 나기 때문입니다.</p>

      <h2>1. 스트레스 DSR이란 무엇이며 왜 도입되었나?</h2>
      <p>일반 DSR은 현재 시점의 약정 금리만을 기준으로 차주의 연간 원리금 상환 능력을 평가합니다. 반면 <strong>스트레스 DSR</strong>은 대출 기간(통상 30~40년) 중 금리가 상승할 위험을 미리 반영하여, 실제 대출 금리에 <strong>가상의 가산금리(스트레스 금리)</strong>를 더한 뒤 DSR 40%(1금융권) 또는 50%(2금융권) 통과 여부를 심사하는 제도입니다.</p>
      <ul>
        <li><strong>핵심 포인트:</strong> 스트레스 금리는 오직 <strong>'대출 한도 심사'</strong>에만 쓰이는 가상 금리입니다. 매달 통장에서 빠져나가는 실제 이자는 은행과 계약한 <strong>약정 금리</strong>로만 부과됩니다.</li>
      </ul>

      <h2>2. 수도권(3.00%p) vs 지방(0.75%p) vs 신용대출(1.50%p) 기준</h2>
      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>구분</th>
              <th>기본 스트레스 금리</th>
              <th>적용 대상 및 세부 기준</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>수도권·규제지역 주담대</strong></td>
              <td><strong>3.00%p</strong> (하한 상향)</td>
              <td>서울·경기·인천 및 규제지역 내 주택담보대출</td>
            </tr>
            <tr>
              <td><strong>지방 비규제지역 주담대</strong></td>
              <td><strong>0.75%p</strong> (2단계 유예)</td>
              <td>비수도권 주택담보대출 (지방 경기 고려 완화 적용)</td>
            </tr>
            <tr>
              <td><strong>신용대출 · 기타대출</strong></td>
              <td><strong>1.50%p</strong> (3단계 표준)</td>
              <td>기존+신규 신용대출 합산 잔액이 <strong>1억 원 초과</strong> 시에만 적용</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>3. 금리 유형별 스트레스 금리 반영 비율 (변동·혼합·주기·고정)</h2>
      <p>차주가 금리 변동 위험이 낮은 상품을 선택할수록 스트레스 금리 반영 비율(가중치)이 낮아져 한도 방어에 유리합니다.</p>
      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>금리 유형</th>
              <th>3단계 반영 비율</th>
              <th>수도권 주담대 실제 가산</th>
              <th>지방 주담대 실제 가산</th>
              <th>한도 유리도</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>순수 고정금리</strong> (만기까지 고정)</td>
              <td>0%</td>
              <td>+0.00%p</td>
              <td>+0.00%p</td>
              <td>⭐⭐⭐⭐⭐ (한도 최대)</td>
            </tr>
            <tr>
              <td><strong>주기형</strong> (5년 주기 금리 재산정)</td>
              <td>40%</td>
              <td>+1.20%p</td>
              <td>+0.30%p</td>
              <td>⭐⭐⭐⭐ (시중은행 주력)</td>
            </tr>
            <tr>
              <td><strong>혼합형</strong> (5년 고정 후 변동 전환)</td>
              <td>80%</td>
              <td>+2.40%p</td>
              <td>+0.60%p</td>
              <td>⭐⭐ (3단계에서 가중치 상향)</td>
            </tr>
            <tr>
              <td><strong>변동금리</strong> (6개월·1년 변동)</td>
              <td>100%</td>
              <td>+3.00%p</td>
              <td>+0.75%p</td>
              <td>⭐ (한도 축소 폭 최대)</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>4. 실전 한도 방어 전략 3가지</h2>
      <ol>
        <li><strong>혼합형 대신 '주기형' 주담대 선택:</strong> 5년 뒤 변동금리로 바뀌는 혼합형(80% 반영)보다 5년 단위로 금리가 재고정되는 주기형(40% 반영)을 선택하면 수도권 기준 가산금리가 2.4%p에서 1.2%p로 절반 줄어들어 대출 한도가 크게 늘어납니다.</li>
        <li><strong>신용대출과 마이너스통장 한도 먼저 정리:</strong> 신용대출은 DSR 산정 만기가 5년으로 짧아 원금 상환 부담이 매우 크게 잡힙니다. 특히 마이너스통장은 '실제 사용한 금액'이 아니라 <strong>'개설된 한도 전체'</strong>가 부채로 잡히므로, 주담대 심사 전 불필요한 마통 한도를 줄이거나 상환하는 것이 유리합니다.</li>
        <li><strong>직접 시뮬레이션으로 비교하기:</strong> ZIPPY가 제공하는 <a href="../tools/dsr.html" style="color:#60a5fa; font-weight:700;">[DSR · 스트레스 DSR 대출 한도 계산기]</a>에서 내 연소득과 기존 부채를 넣고 4가지 금리 유형별 최대 한도를 즉시 비교해 보세요.</li>
      </ol>
"""
    },
    {
        "slug": "interest-rate-and-market",
        "badge": "거시경제 핵심",
        "title": "기준금리 인상·인하가 주가·환율·채권·부동산에 미치는 구조 완벽 정리",
        "desc": "한국은행 금융통화위원회와 미국 연준(Fed)의 기준금리 결정이 코스피, 나스닥, 원·달러 환율, 채권 가격 및 대출 금리에 전달되는 경로를 알기 쉽게 해설합니다.",
        "body": """
      <p>경제 뉴스에서 가장 자주 등장하는 단어는 단연 <strong>기준금리(Base Rate)</strong>입니다. 미국 연방준비제도(Fed·연준)의 FOMC 회의나 한국은행 금융통화위원회의 결정에 따라 하루아침에 주가지수와 환율이 요동치는 이유는 기준금리가 곧 <strong>'돈의 가격(할인율)'</strong>이기 때문입니다.</p>

      <h2>1. 기준금리가 시장에 전달되는 4단계 경로</h2>
      <p>중앙은행이 기준금리를 조정하면 시중은행 간 초단기 자금 거래 금리(콜금리)가 즉시 변하고, 이어서 은행채·국고채 등 채권 금리와 코픽스(COFIX)가 움직이며 최종적으로 가계·기업의 대출 및 예·적금 금리에 반영됩니다.</p>

      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>자산군 / 지표</th>
              <th>기준금리 인상 시 (긴축)</th>
              <th>기준금리 인하 시 (완화)</th>
              <th>핵심 작동 원리</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>성장주·기술주 (나스닥 등)</strong></td>
              <td>하락 압력 (할인율 상승)</td>
              <td>상승 동력 (유동성 유입)</td>
              <td>미래 현금흐름의 현재가치 할인율 변화</td>
            </tr>
            <tr>
              <td><strong>기존 발행 채권 가격</strong></td>
              <td>하락 (채권 금리와 역방향)</td>
              <td>상승 (자본차익 발생)</td>
              <td>신규 고금리 채권 대비 구채권 매력도 변화</td>
            </tr>
            <tr>
              <td><strong>통화 가치 (환율)</strong></td>
              <td>자국 통화 강세 요인</td>
              <td>자국 통화 약세 요인</td>
              <td>더 높은 금리를 주는 통화로 글로벌 자금 이동</td>
            </tr>
            <tr>
              <td><strong>부동산·실물자산</strong></td>
              <td>매수세 위축·금융비용 증가</td>
              <td>차입 비용 하락·수요 회복</td>
              <td>주택담보대출 이자 부담 및 레버리지 비용</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. 왜 채권 금리와 채권 가격은 반대로 움직일까?</h2>
      <p>많은 초보 투자자가 헷갈려하는 부분이 <strong>"금리가 오르면 채권 가격이 떨어진다"</strong>는 원리입니다. 예를 들어 만기 시 100만 원과 연 3% 이자(3만 원)를 주는 기존 채권이 있다고 가정해 봅시다. 만약 시중 금리가 5%로 오르면, 새로 발행되는 채권은 연 5만 원의 이자를 줍니다. 이때 연 3만 원만 주는 기존 채권을 팔려면 가격을 깎아주어야만 매수자가 나타나므로 <strong>기존 채권의 시장 가격은 하락</strong>하게 됩니다.</p>

      <h2>3. 한·미 금리 역전과 외국인 수급</h2>
      <p>한국의 기준금리가 미국의 기준금리보다 낮아지는 <strong>한·미 금리 역전</strong>이 깊어지면, 글로벌 투자자 입장에서는 원화 자산보다 달러 채권·예금의 무위험 수익률이 높아집니다. 다만 실제 외국인 주식 자금 유출입은 단순 금리 격차뿐 아니라 <strong>국내 반도체·수출 기업의 이익 모멘텀과 경상수지 흑자 규모</strong>에 의해 복합적으로 결정됩니다.</p>
"""
    },
    {
        "slug": "exchange-rate-and-current-account",
        "badge": "외환·무역",
        "title": "원·달러 환율 결정 요인과 경상수지·외환보유액으로 읽는 한국 경제 기초체력",
        "desc": "원·달러 환율이 1,400원대를 오르내리는 구조적 배경(서학개미 달러 수요, 달러 인덱스, 경상수지 흑자)과 환율이 수출기업 및 수입물가에 미치는 영향을 분석합니다.",
        "body": """
      <p>원·달러 환율은 한국 경제의 체온계로 불립니다. 과거에는 환율 급등이 곧 외환위기나 외국인 자금 이탈의 신호로만 해석되었으나, 최근에는 <strong>국내 개인·기관 투자자의 해외주식 투자(서학개미 달러 환전 수요)</strong>와 글로벌 강달러 현상 등 구조적 수급 요인이 함께 작동하고 있습니다.</p>

      <h2>1. 원·달러 환율을 움직이는 4대 수급 엔진</h2>
      <ol>
        <li><strong>글로벌 달러 인덱스(DXY):</strong> 미국 경제가 유럽·일본보다 강하거나 미국 국채 금리가 높을 때 달러 자체가 강세를 보이며 원화 환율을 밀어 올립니다.</li>
        <li><strong>경상수지(무역수지) 흑자:</strong> 반도체·자동차·조선 등 수출 기업이 해외에서 벌어들이는 달러가 많을수록 국내 외환시장에 달러 공급이 늘어나 환율 급등을 막아주는 버팀목(하방 경직성) 역할을 합니다.</li>
        <li><strong>내국인의 해외 증권 투자:</strong> 개인 투자자와 국민연금 등 연기금이 미국 주식·채권을 매수하기 위해 원화를 팔고 달러를 사는 규모가 커질수록 환율 하단이 높아집니다.</li>
        <li><strong>외국인 국내 증시 순매수/순매도:</strong> 외국인이 코스피 대형주를 대규모로 매도하고 달러로 환전해 나가는 시기에는 단기 환율 변동성이 확대됩니다.</li>
      </ol>

      <h2>2. 고환율의 양면성: 수출 채산성 vs 수입 물가</h2>
      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>부문</th>
              <th>고환율(원화 약세)의 긍정적 영향</th>
              <th>고환율(원화 약세)의 부정적 영향</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>수출 대기업 (반도체·자동차·조선)</strong></td>
              <td>달러 매출의 원화 환산 이익 증가 및 가격 경쟁력 개선</td>
              <td>해외 원자재 도입 단가 및 외화 부채 상환 부담 증가</td>
            </tr>
            <tr>
              <td><strong>내수·에너지·항공·음식료</strong></td>
              <td>방한 외국인 관광객 증가 효과</td>
              <td>원유·가스·곡물 수입 단가 상승 → 소비자물가 자극</td>
            </tr>
            <tr>
              <td><strong>개인 해외주식 투자자</strong></td>
              <td>보유 달러 자산의 원화 평가액 상승(환차익 발생)</td>
              <td>신규 달러 환전 매수 시 부담 증가 및 환차익 과세 반영</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>해외주식 매매 시 환율 변동이 내 세후 수익에 미치는 영향은 <a href="../tools/us-stock-tax.html" style="color:#60a5fa; font-weight:700;">[해외주식 환차익 · 양도소득세 계산기]</a>에서 정확히 시뮬레이션해 볼 수 있습니다.</p>
"""
    },
    {
        "slug": "us-cpi-and-employment-indicators",
        "badge": "글로벌 경제지표",
        "title": "미국 CPI(소비자물가)·PCE·고용지표 발표 시 나스닥과 코스피가 움직이는 원리",
        "desc": "매달 발표되는 미국 소비자물가지수(CPI), 근원 개인소비지출(Core PCE), 비농업 고용보고서(NFP) 및 실업률을 해석하는 방법과 증시 반응 공식을 정리합니다.",
        "body": """
      <p>미국 연방준비제도(Fed)의 통화정책 목표는 법률상 두 가지(Dual Mandate)로 정해져 있습니다. 바로 <strong>'물가 안정(연 2% 목표)'</strong>과 <strong>'최대 고용'</strong>입니다. 따라서 매달 발표되는 물가 지표와 고용 지표는 연준의 다음 금리 결정을 예고하는 가장 강력한 힌트가 됩니다.</p>

      <h2>1. 3대 핵심 물가·고용 지표 비교</h2>
      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>지표명</th>
              <th>발표 기관 및 시기</th>
              <th>핵심 체크 포인트</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>소비자물가지수 (CPI)</strong></td>
              <td>미 노동통계국(BLS) / 매월 중순</td>
              <td>변동성이 큰 식료품·에너지를 제외한 <strong>근원(Core) CPI</strong>와 주거비 추이</td>
            </tr>
            <tr>
              <td><strong>개인소비지출 (PCE) 물가</strong></td>
              <td>미 상무부(BEA) / 매월 말</td>
              <td>연준이 금리 결정 시 가장 중시하는 공식 인플레이션 기준 지표</td>
            </tr>
            <tr>
              <td><strong>비농업 고용보고서 (NFP)</strong></td>
              <td>미 노동통계국(BLS) / 매월 첫째 금요일</td>
              <td>신규 취업자 수 증감, 실업률(%), 시간당 평균임금 상승률</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. 왜 '나쁜 경제 지표'에 주가가 오르기도 할까? (Good News is Bad News)</h2>
      <p>인플레이션 우려가 높은 국면에서는 고용이 예상보다 너무 뜨겁게 나오면 <em>"임금 상승 → 소비 과열 → 물가 재반등 → 금리 인하 지연"</em>으로 해석되어 기술주(나스닥)가 하락하곤 합니다. 반대로 물가가 안정된 이후 경기 침체 우려가 커진 국면에서는 고용 지표가 급격히 악화될 경우 경기 침체 공포로 증시가 동반 하락합니다. 즉, <strong>현재 시장의 주된 걱정이 '인플레이션'인지 '경기 침체'인지</strong>를 먼저 파악해야 지표 발표 후 시장 방향을 정확히 읽을 수 있습니다.</p>
"""
    },
    {
        "slug": "etf-and-leverage-decay-risk",
        "badge": "ETF·투자전략",
        "title": "서학개미 필독: 미국 지수 ETF(S&P500·나스닥100)와 3배 레버리지(SOXL·TQQQ) 음의 복리 리스크",
        "desc": "국내 투자자들이 가장 많이 매수하는 S&P500·나스닥100 지수 추종 ETF와 3배 레버리지 ETF(SOXL, TQQQ)의 일일 리밸런싱 구조 및 횡보장 음의 복리(Volatility Decay) 원리를 수학적으로 해설합니다.",
        "body": """
      <p>최근 국내 투자자들 사이에서 TIGER·KODEX 미국S&amp;P500 등 1배수 지수 추종 ETF와 함께, 미국 반도체 지수 일일 수익률의 3배를 추종하는 <strong>SOXL</strong>이나 나스닥100 3배를 추종하는 <strong>TQQQ</strong>에 대한 집중 투자가 크게 늘었습니다. 그러나 레버리지 ETF는 기초지수가 제자리에 돌아와도 원금이 녹아내리는 구조적 특성을 정확히 이해하고 접근해야 합니다.</p>

      <h2>1. 레버리지 ETF의 '음의 복리(변동성 잠식)' 수치 예시</h2>
      <p>레버리지 ETF는 '장기 누적 수익률의 3배'가 아니라 <strong>'하루(일간) 등락률의 3배'</strong>만을 추종하도록 매일 리밸런싱됩니다. 기초지수가 첫날 10% 오르고 둘째 날 9.09% 내리면 기초지수는 원래 가격(100)으로 돌아오지만, 3배 레버리지는 어떻게 되는지 비교해 보겠습니다.</p>

      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>구분</th>
              <th>시작 가격</th>
              <th>1일차 (지수 +10%)</th>
              <th>2일차 (지수 -9.09%)</th>
              <th>최종 결과</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>1배 기초지수 ETF</strong></td>
              <td>100.0</td>
              <td>110.0 (+10.0%)</td>
              <td>100.0 (-9.09%)</td>
              <td><strong>100.0 (원금 유지 0%)</strong></td>
            </tr>
            <tr>
              <td><strong>2배 레버리지 ETF</strong></td>
              <td>100.0</td>
              <td>120.0 (+20.0%)</td>
              <td>98.18 (-18.18%)</td>
              <td><strong>98.18 (-1.82% 손실)</strong></td>
            </tr>
            <tr>
              <td><strong>3배 레버리지 (SOXL·TQQQ)</strong></td>
              <td>100.0</td>
              <td>130.0 (+30.0%)</td>
              <td>94.55 (-27.27%)</td>
              <td><strong>94.55 (-5.45% 손실)</strong></td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. 안전한 장기 자산 배분 원칙</h2>
      <ul>
        <li><strong>코어-새틀라이트(Core-Satellite) 전략:</strong> 전체 포트폴리오의 70~80% 이상은 S&amp;P500이나 나스닥100 같은 1배수 우량 지수 ETF로 적립식 복리 투자를 유지하고, 변동성 상품은 감내 가능한 소액 비중으로만 단기 추세 구간에 활용하는 것이 계좌 파산을 막는 핵심입니다.</li>
        <li>1배수 우량 지수 ETF를 매월 꾸준히 적립했을 때의 10~20년 뒤 자산 규모는 <a href="../tools/compound.html" style="color:#60a5fa; font-weight:700;">[복리 · 적립식 투자 수익률 계산기]</a>에서 확인해 보세요.</li>
      </ul>
"""
    },
    {
        "slug": "valuation-per-pbr-roe",
        "badge": "주식 기초지표",
        "title": "주식 투자 핵심 지표: PER·PBR·ROE·EPS로 저평가 우량주와 밸류업 종목 읽는 법",
        "desc": "기업 실적 발표와 밸류업 공시에서 반드시 확인해야 할 주가수익비율(PER), 주가순자산비율(PBR), 자기자본이익률(ROE)의 계산 공식과 업종별 해석 기준을 설명합니다.",
        "body": """
      <p>주가가 비싼지 싼지를 판단할 때 단순히 '1주당 가격(예: 5만 원 vs 50만 원)'만 비교하는 것은 의미가 없습니다. 기업의 전체 시가총액이 그 기업이 벌어들이는 <strong>순이익(PER)</strong>이나 보유한 <strong>순자산(PBR)</strong>에 비해 몇 배로 거래되고 있는지를 봐야 합니다.</p>

      <h2>1. 4대 밸류에이션 지표 한눈에 비교</h2>
      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>지표</th>
              <th>산출 공식</th>
              <th>쉬운 해석</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>EPS (주당순이익)</strong></td>
              <td>당기순이익 ÷ 발행주식수</td>
              <td>주식 1주가 1년 동안 벌어들인 순이익 금액</td>
            </tr>
            <tr>
              <td><strong>PER (주가수익비율)</strong></td>
              <td>주가 ÷ EPS (또는 시가총액 ÷ 순이익)</td>
              <td>현재 순이익 수준으로 시가총액만큼 버는 데 걸리는 연수(배수)</td>
            </tr>
            <tr>
              <td><strong>PBR (주가순자산비율)</strong></td>
              <td>주가 ÷ BPS (또는 시가총액 ÷ 자본총계)</td>
              <td>기업이 가진 순자산(청산가치) 대비 주가가 몇 배인지 표시 (1배 미만은 장부가 이하)</td>
            </tr>
            <tr>
              <td><strong>ROE (자기자본이익률)</strong></td>
              <td>(당기순이익 ÷ 자기자본) × 100</td>
              <td>주주가 맡긴 자본금으로 연 몇 %의 이익을 냈는가 (자본 효율성)</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. 저PER·저PBR 함정(Value Trap)을 피하는 법</h2>
      <p>PER이나 PBR이 낮다고 무조건 좋은 주식은 아닙니다. 사양 산업이거나 향후 이익이 급감할 것으로 예상되는 기업은 시장이 낮은 배수를 부여합니다. 따라서 <strong>① ROE가 자본비용(통상 8~10%) 이상으로 꾸준히 유지되는지</strong>, <strong>② 과거 실적(Trailing PER)이 아니라 향후 12개월 예상 실적(Forward PER) 기준으로도 저평가인지</strong>, <strong>③ 배당 성향과 자사주 소각 등 주주환원율이 개선되고 있는지</strong>를 함께 점검해야 합니다.</p>
"""
    },
    {
        "slug": "semiconductor-cycle-and-ai-export",
        "badge": "산업·수출분석",
        "title": "한국 경제와 코스피의 핵심 엔진: 메모리 반도체(HBM·DRAM) 사이클과 수출 지표 읽는 법",
        "desc": "삼성전자·SK하이닉스 등 한국 반도체 산업의 실적과 코스피 지수를 좌우하는 글로벌 AI 데이터센터 설비투자(CAPEX), HBM·범용 DRAM 사이클, 매월 1일 한국 수출입 동향 해석법을 정리합니다.",
        "body": """
      <p>대한민국 전체 수출에서 반도체가 차지하는 비중은 약 20%에 달하며, 코스피 전체 시가총액에서도 반도체 투톱(삼성전자·SK하이닉스)과 소부장(소재·부품·장비) 밸류체인이 차지하는 비중은 절대적입니다. 따라서 한국 증시와 원화 환율의 중장기 방향을 읽으려면 반도체 사이클의 선행 지표를 이해해야 합니다.</p>

      <h2>1. AI 데이터센터 투자(CAPEX)와 HBM·DRAM의 연결고리</h2>
      <ul>
        <li><strong>빅테크 설비투자(CAPEX):</strong> 마이크로소프트, 알파벳(구글), 아마존, 메타 등 미국 하이퍼스케일러들의 분기별 AI 데이터센터 자본지출 규모가 한국 고대역폭메모리(HBM) 및 기업용 SSD(eSSD) 수요의 가장 정확한 선행 지표입니다.</li>
        <li><strong>HBM과 범용 DRAM의 차별화:</strong> HBM은 고객사 주문 기반 선계약 구조로 수익성이 높고 변동성이 낮은 반면, PC·스마트폰용 범용 DRAM(DDR4·DDR5)은 글로벌 소비 경기와 중화권 메모리 업체의 공급량에 민감하게 반응합니다.</li>
      </ul>

      <h2>2. 매월 1일·11일·21일 관세청 수출입 동향 체크 포인트</h2>
      <p>관세청과 산업통상자원부가 발표하는 월간 및 10일 단위 수출입 통계에서 <strong>① 반도체 수출액 전년 동기 대비 증감률(YoY)</strong>과 <strong>② 일평균 수출액 추이</strong>를 확인하면 기업들의 분기 잠정실적 발표보다 한 달 먼저 이익 방향성을 가늠할 수 있습니다.</p>
"""
    },
    {
        "slug": "compound-interest-and-tax-saving",
        "badge": "자산관리·절세",
        "title": "복리의 마법과 절세 계좌(ISA·연금저축·IRP) 및 해외주식 양도세 250만 원 공제 활용법",
        "desc": "장기 투자에서 수익률 못지않게 중요한 과세이연·비과세 효과를 극대화하기 위한 ISA, 연금저축펀드, 직투(해외주식 직접투자) 계좌별 세금 구조와 최적 활용 순서를 비교합니다.",
        "body": """
      <p>똑같이 연 8% 수익을 내는 ETF에 투자하더라도 <strong>어떤 계좌에 담느냐</strong>에 따라 10년·20년 뒤 손에 쥐는 세후 자산은 수천만 원 이상 벌어집니다. 배당소득세(15.4%)나 해외주식 양도소득세(22%)로 매년 빠져나갈 세금을 아껴 다시 복리로 굴리는 <strong>과세이연(Tax Deferral)</strong> 효과 때문입니다.</p>

      <h2>1. 국내 투자자가 활용 가능한 3대 투자 계좌 비교</h2>
      <div class="table-wrap">
        <table class="info-table">
          <thead>
            <tr>
              <th>계좌 구분</th>
              <th>주요 투자 대상</th>
              <th>세금 혜택 및 과세 방식</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>ISA (중개형)</strong></td>
              <td>국내 주식 및 국내 상장 해외 ETF</td>
              <td>순이익 200만 원(서민형 400만 원)까지 비과세, 초과분 9.9% 분리과세 및 손익통산</td>
            </tr>
            <tr>
              <td><strong>연금저축펀드 · IRP</strong></td>
              <td>국내 상장 해외 ETF 및 TDF 등</td>
              <td>연간 납입액 세액공제(13.2%~16.5%) + 운용 중 과세이연 + 55세 이후 연금소득세(3.3%~5.5%)</td>
            </tr>
            <tr>
              <td><strong>해외주식 직접투자(일반)</strong></td>
              <td>미국 등 해외 상장 주식·ETF</td>
              <td>연간 실현 순이익 <strong>250만 원 기본공제</strong> 후 초과분에 22% 양도소득세 (금융소득종합과세 미포함)</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>2. 실전 시뮬레이션 도구 활용</h2>
      <ul>
        <li>매월 적립식 투자 시 세전·세후 복리 자산 성장은 <a href="../tools/compound.html" style="color:#60a5fa; font-weight:700;">[복리 · 적립식 투자 수익률 계산기]</a>에서 확인하세요.</li>
        <li>미국 주식 직접투자 시 환율 변동을 포함한 양도세 계산은 <a href="../tools/us-stock-tax.html" style="color:#60a5fa; font-weight:700;">[해외주식 환차익 · 양도소득세 계산기]</a>에서 즉시 계산할 수 있습니다.</li>
      </ul>
"""
    },
]


def build_guides() -> list[str]:
    """/guide/index.html 및 8편의 상설 경제 가이드 HTML 생성 후 상대 경로 리스트 반환."""
    os.makedirs(GUIDE_DIR, exist_ok=True)
    generated = []

    for g in GUIDES_DATA:
        slug = g["slug"]
        rel_path = f"guide/{slug}.html"
        other_links = "".join(
            f'<li><a href="./{og["slug"]}.html" style="color:#60a5fa;">{_html.escape(og["title"])}</a></li>'
            for og in GUIDES_DATA
            if og["slug"] != slug
        )
        body_html = f"""
    <main class="card">
      <span class="badge">{_html.escape(g["badge"])}</span>
      <h1>{_html.escape(g["title"])}</h1>
      <p style="color:var(--text-muted); font-size:0.86rem; margin-bottom:1.1rem;">
        ZIPPY 경제·투자 심층 가이드 · <a href="./index.html" style="color:#60a5fa;">전체 가이드 목록 보기</a>
      </p>
      {g["body"]}
      <hr style="border:none; border-top:1px solid var(--border); margin:1.8rem 0 1.2rem;" />
      <h3>📚 함께 읽으면 좋은 ZIPPY 경제 가이드</h3>
      <ul>{other_links}</ul>
    </main>
"""
        json_ld = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": g["title"],
            "description": g["desc"],
            "author": {"@type": "Organization", "name": "ZIPPY Economy Briefing"},
            "publisher": {"@type": "Organization", "name": "ZIPPY", "url": "https://zippy.kr"},
            "mainEntityOfPage": f"https://zippy.kr/{rel_path}",
        }
        full_html = _render_layout(
            title=f"{g['title']} | ⚡ ZIPPY 경제 가이드",
            description=g["desc"],
            canonical_path=rel_path,
            active_nav="guide",
            body_html=body_html,
            rel_root="..",
            json_ld=json_ld,
        )
        with open(os.path.join(GUIDE_DIR, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(full_html)
        generated.append(rel_path)

    # 가이드 인덱스 (/guide/index.html)
    cards = "".join(
        f"""
        <a href="./{g['slug']}.html" class="link-card">
          <span class="badge">{_html.escape(g['badge'])}</span>
          <h3 style="margin:0.2rem 0 0.45rem; font-size:1.06rem;">{_html.escape(g['title'])}</h3>
          <p style="margin:0; color:var(--text-muted); font-size:0.88rem;">{_html.escape(g['desc'])}</p>
        </a>
        """
        for g in GUIDES_DATA
    )
    index_body = f"""
    <main>
      <div class="card">
        <span class="badge">EVERGREEN ECONOMIC GUIDES</span>
        <h1>📘 경제 · 투자 · 금융 규제 핵심 가이드</h1>
        <p style="margin:0; color:var(--text-muted);">
          데일리 경제 뉴스를 더 깊이 이해하기 위해 반드시 알아야 할 거시경제 지표, 스트레스 DSR 3단계 대출 규제, 환율·반도체 사이클, ETF·절세 전략을 정리한 상설 해설 아카이브입니다.
        </p>
      </div>
      <div class="grid-2">
        {cards}
      </div>
    </main>
"""
    index_html = _render_layout(
        title="경제·투자 핵심 가이드 모음 (금리·환율·스트레스 DSR·ETF·반도체) | ⚡ ZIPPY",
        description="기준금리, 원·달러 환율, 미국 CPI·고용지표, 2026 스트레스 DSR 3단계, 레버리지 ETF 리스크, PER·PBR 밸류에이션 등 핵심 경제 가이드를 한곳에서 확인하세요.",
        canonical_path="guide/index.html",
        active_nav="guide",
        body_html=index_body,
        rel_root="..",
    )
    with open(os.path.join(GUIDE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    generated.insert(0, "guide/index.html")
    return generated


# =====================================================================
# 2. 일자별 브리핑 아카이브 페이지 생성 및 Git 히스토리 백필
# =====================================================================
def _build_executive_summary(period_data: dict) -> str:
    """국내 Top 3 및 해외 Top 3 이슈의 헤드라인을 연결하여 하루 시장 총평 문단 생성."""
    dom = period_data.get("domestic") or {}
    gl = period_data.get("global") or {}
    dom_kws = dom.get("keywords") or []
    gl_kws = gl.get("keywords") or []
    dom_res = dom.get("results") or {}
    gl_res = gl.get("results") or {}

    dom_heads = [dom_res.get(k, {}).get("headline") or k for k in dom_kws if k]
    gl_heads = [gl_res.get(k, {}).get("headline") or k for k in gl_kws if k]

    parts = []
    if dom_heads:
        parts.append(
            f"국내 경제·금융 시장에서는 <strong>'{_html.escape(dom_heads[0])}'</strong> 이슈를 필두로 "
            + (
                ", ".join(f"'{_html.escape(h)}'" for h in dom_heads[1:])
                + " 등이 핵심 쟁점으로 부각되었습니다."
                if len(dom_heads) > 1
                else "주요 수급 및 정책 변수가 부각되었습니다."
            )
        )
    if gl_heads:
        parts.append(
            f"글로벌 시장에서는 <strong>'{_html.escape(gl_heads[0])}'</strong> 흐름과 함께 "
            + (
                ", ".join(f"'{_html.escape(h)}'" for h in gl_heads[1:])
                + " 이슈가 맞물리며 국내 증시·환율 및 수출 산업 전반에 직간접적인 파급력을 미치고 있습니다."
                if len(gl_heads) > 1
                else "거시경제 지표와 통화·통상 정책 변화가 주목받고 있습니다."
            )
        )
    return " ".join(parts) if parts else "국내외 주요 경제·금융 핵심 이슈를 종합 분석한 데일리 브리핑입니다."


def _render_region_section_html(region_title: str, region_data: dict) -> str:
    kws = region_data.get("keywords") or []
    results = region_data.get("results") or {}
    news_map = region_data.get("news") or {}
    if not kws:
        return ""

    blocks = []
    for idx, kw in enumerate(kws, 1):
        item = results.get(kw) or {}
        arts = news_map.get(kw) or []
        headline = _html.escape(item.get("headline") or f"{kw} 핵심 이슈")
        facts = item.get("facts") or []
        facts_li = "".join(f"<li>{_html.escape(str(f))}</li>" for f in facts)

        qna = item.get("qna") or []
        qna_html = "".join(
            f'<div class="qna-item">'
            f'<div class="qna-q">Q{qi}. {_html.escape(str(qa.get("q", "")))}</div>'
            f'<blockquote class="qna-a"><strong>A.</strong> {_html.escape(str(qa.get("a", "")))}</blockquote>'
            f'</div>'
            for qi, qa in enumerate(qna, 1)
        )

        watch = item.get("watch") or []
        watch_html = (
            '<h4>👀 향후 핵심 관전 포인트</h4><ul>'
            + "".join(f"<li>{_html.escape(str(w))}</li>" for w in watch)
            + "</ul>"
            if watch
            else ""
        )

        arts_html = ""
        if arts:
            art_items = "".join(
                f'<li><a href="{_html.escape(str(a.get("link", "#")))}" target="_blank" rel="noopener" style="color:#93c5fd;">'
                f'{_html.escape(str(a.get("title", "")))}</a></li>'
                for a in arts[:6]
            )
            arts_html = f'<details style="margin-top:0.75rem; font-size:0.86rem; color:var(--text-muted);"><summary style="cursor:pointer;">🔗 참고 언론 보도 원문 ({len(arts)}건)</summary><ul style="margin-top:0.4rem;">{art_items}</ul></details>'

        blocks.append(
            f"""
      <article class="card">
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.6rem;">
          <h3 style="margin:0; font-size:1.15rem;">📌 {idx}. {headline}</h3>
          <span class="kw-badge">{_html.escape(kw)}</span>
        </div>
        <h4>📌 무슨 일이 있었나 (핵심 팩트)</h4>
        <ul>{facts_li}</ul>
        <h4>💬 핵심 Q&amp;A 심층 해설</h4>
        {qna_html}
        {watch_html}
        {arts_html}
      </article>
"""
        )

    return f"<h2>{_html.escape(region_title)}</h2>\n" + "\n".join(blocks)


def write_archive_page(date_str: str, period_data: dict, prev_date: str | None = None, next_date: str | None = None) -> str:
    """특정 날짜(YYYY-MM-DD)의 독립 정적 아카이브 페이지 생성."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    period_label = period_data.get("period_label") or date_str
    dom_data = period_data.get("domestic") or {}
    gl_data = period_data.get("global") or {}
    dom_kws = dom_data.get("keywords") or []
    gl_kws = gl_data.get("keywords") or []

    exec_summary = _build_executive_summary(period_data)
    dom_badges = "".join(f'<span class="kw-badge">{_html.escape(k)}</span>' for k in dom_kws)
    gl_badges = "".join(f'<span class="kw-badge">{_html.escape(k)}</span>' for k in gl_kws)

    dom_section = _render_region_section_html("🇰🇷 국내 주요 경제 이슈 Top 3", dom_data)
    gl_section = _render_region_section_html("🌐 해외·글로벌 경제 이슈 Top 3", gl_data)

    nav_prev = f'<a href="./{prev_date}.html" style="color:#60a5fa; text-decoration:none; font-weight:600;">← {prev_date} 브리핑</a>' if prev_date else '<span></span>'
    nav_next = f'<a href="./{next_date}.html" style="color:#60a5fa; text-decoration:none; font-weight:600;">{next_date} 브리핑 →</a>' if next_date else '<span></span>'

    body_html = f"""
    <main>
      <div class="card">
        <span class="badge">BRIEFING ARCHIVE · {_html.escape(date_str)}</span>
        <h1>📅 {_html.escape(period_label)} 국내·해외 실시간 경제 브리핑</h1>
        <p style="margin:0.5rem 0 0.9rem;">{exec_summary}</p>
        <div style="font-size:0.88rem; margin-bottom:0.35rem;"><strong>🇰🇷 국내 키워드:</strong> {dom_badges}</div>
        <div style="font-size:0.88rem;"><strong>🌐 해외 키워드:</strong> {gl_badges}</div>
      </div>

      {dom_section}
      {gl_section}

      <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem;">
        {nav_prev}
        <a href="./index.html" style="color:var(--text-main); font-weight:700; text-decoration:none;">🗂️ 전체 아카이브 목록</a>
        {nav_next}
      </div>
    </main>
"""
    lead_kw = ", ".join((dom_kws + gl_kws)[:4])
    title = f"{period_label} 경제 브리핑 ({lead_kw}) | ⚡ ZIPPY 아카이브"
    desc = f"{period_label} 국내·해외 핵심 경제 이슈 브리핑: {lead_kw} 관련 핵심 팩트와 심층 Q&A 3선, 향후 관전 포인트를 확인하세요."
    rel_path = f"archive/{date_str}.html"

    json_ld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title,
        "datePublished": f"{date_str}T09:00:00+09:00",
        "dateModified": f"{date_str}T20:00:00+09:00",
        "description": desc,
        "author": {"@type": "Organization", "name": "ZIPPY Economy Briefing"},
        "publisher": {"@type": "Organization", "name": "ZIPPY", "url": "https://zippy.kr"},
        "mainEntityOfPage": f"https://zippy.kr/{rel_path}",
    }

    html_out = _render_layout(
        title=title,
        description=desc,
        canonical_path=rel_path,
        active_nav="archive",
        body_html=body_html,
        rel_root="..",
        json_ld=json_ld,
    )
    with open(os.path.join(ARCHIVE_DIR, f"{date_str}.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    return rel_path


def _load_archive_meta_store() -> dict:
    meta_path = os.path.join(ARCHIVE_DIR, "archive_data.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_archive_meta_store(store: dict):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    meta_path = os.path.join(ARCHIVE_DIR, "archive_data.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def rebuild_all_archives(extra_periods: list[dict] | None = None) -> list[str]:
    """저장된 일자별 브리핑 데이터와 신규 period 데이터를 합쳐 전체 아카이브 HTML 및 목록 페이지 갱신."""
    store = _load_archive_meta_store()

    if extra_periods:
        for p in extra_periods:
            if not isinstance(p, dict):
                continue
            d_str = p.get("start_date")
            end_str = p.get("end_date")
            # 단일 일자 브리핑만 일자별 아카이브에 등록 (start_date == end_date)
            if not d_str or (end_str and d_str != end_str):
                continue
            dom_kws = (p.get("domestic") or {}).get("keywords") or []
            if not dom_kws:
                continue
            store[d_str] = p

    _save_archive_meta_store(store)
    sorted_dates = sorted(store.keys())
    generated_paths = []

    for i, d_str in enumerate(sorted_dates):
        prev_d = sorted_dates[i - 1] if i > 0 else None
        next_d = sorted_dates[i + 1] if i + 1 < len(sorted_dates) else None
        rel = write_archive_page(d_str, store[d_str], prev_d, next_d)
        generated_paths.append(rel)

    # 아카이브 인덱스 (/archive/index.html) 최신순 렌더링
    cards_html = []
    for d_str in reversed(sorted_dates):
        p = store[d_str]
        p_label = p.get("period_label") or d_str
        dom = p.get("domestic") or {}
        gl = p.get("global") or {}
        dom_kws = dom.get("keywords") or []
        gl_kws = gl.get("keywords") or []
        dom_res = dom.get("results") or {}
        lead_head = ""
        if dom_kws:
            lead_head = (dom_res.get(dom_kws[0]) or {}).get("headline") or dom_kws[0]

        kw_badges = "".join(f'<span class="kw-badge">🇰🇷 {_html.escape(k)}</span>' for k in dom_kws) + "".join(
            f'<span class="kw-badge">🌐 {_html.escape(k)}</span>' for k in gl_kws
        )
        cards_html.append(
            f"""
        <a href="./{d_str}.html" class="link-card" style="margin-bottom:0.85rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.4rem; margin-bottom:0.35rem;">
            <strong style="color:#60a5fa; font-size:1.02rem;">📅 {_html.escape(p_label)} 경제 브리핑</strong>
            <span style="font-size:0.8rem; color:var(--text-muted);">전체 원고 보기 →</span>
          </div>
          <div style="font-weight:700; font-size:1.05rem; margin-bottom:0.55rem;">{_html.escape(lead_head)} 외 국내·해외 주요 이슈 6선</div>
          <div>{kw_badges}</div>
        </a>
"""
        )

    index_body = f"""
    <main>
      <div class="card">
        <span class="badge">BRIEFING ARCHIVE ({len(sorted_dates)}일 누적)</span>
        <h1>🗂️ 일자별 경제 브리핑 아카이브</h1>
        <p style="margin:0; color:var(--text-muted);">
          매일 업데이트된 국내 Top 3 및 해외 Top 3 핵심 경제 이슈와 심층 Q&amp;A 해설을 날짜별로 열람하실 수 있습니다.
        </p>
      </div>
      <div>
        {"".join(cards_html)}
      </div>
    </main>
"""
    index_out = _render_layout(
        title="일자별 경제 브리핑 아카이브 목록 | ⚡ ZIPPY",
        description="ZIPPY가 매일 발행한 국내·해외 핵심 경제 이슈 브리핑과 심층 Q&A 아카이브를 날짜별로 확인하세요.",
        canonical_path="archive/index.html",
        active_nav="archive",
        body_html=index_body,
        rel_root="..",
    )
    with open(os.path.join(ARCHIVE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_out)

    return ["archive/index.html"] + list(reversed(generated_paths))


def backfill_archives_from_git() -> int:
    """Git 커밋 히스토리에 남아 있는 news_data.json 스냅샷을 스캔하여 과거 일자별 브리핑을 복원."""
    try:
        res = subprocess.run(
            ["git", "log", "--format=%H", "-n", "120", "--", "news_data.json"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        commits = [c.strip() for c in res.stdout.splitlines() if c.strip()]
    except Exception as e:
        print(f"[백필 경고] git log 조회 실패: {e}")
        return 0

    store = _load_archive_meta_store()
    added = 0
    # 오래된 커밋부터 최신 커밋 순으로 순회하여 해당 날짜의 가장 최신(마감) 데이터로 덮어쓰기
    for sha in reversed(commits):
        try:
            show_res = subprocess.run(
                ["git", "show", f"{sha}:news_data.json"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if show_res.returncode != 0 or not show_res.stdout:
                continue
            data = json.loads(show_res.stdout)
            for key in ("yesterday", "today"):
                p = data.get(key)
                if not isinstance(p, dict):
                    continue
                d_str = p.get("start_date")
                e_str = p.get("end_date")
                if not d_str or (e_str and d_str != e_str):
                    continue
                dom_kws = (p.get("domestic") or {}).get("keywords") or []
                if len(dom_kws) >= 2:
                    if d_str not in store:
                        added += 1
                    store[d_str] = p
        except Exception:
            continue

    _save_archive_meta_store(store)
    rebuild_all_archives()
    return len(store)


# =====================================================================
# 3. 메인 index.html 프리렌더링(국내+해외 전체 펼침) & sitemap.xml 생성
# =====================================================================
def _build_main_prerendered_cards(today_data: dict) -> str:
    """index.html의 PRE_RENDERED_START ~ END 구간에 들어갈 정적 HTML (국내 3 + 해외 3 모두 open 상태)."""
    blocks = []
    exec_sum = _build_executive_summary(today_data)
    p_label = _html.escape(today_data.get("period_label") or "오늘")
    blocks.append(
        f'<div class="exec-summary-box" style="background:rgba(59,130,246,0.08); border:1px solid rgba(59,130,246,0.3); border-radius:10px; padding:1rem 1.15rem; margin-bottom:1rem; font-size:0.93rem;">'
        f'<strong>📊 {_html.escape(p_label)} 시장 핵심 맥락 총평:</strong> {exec_sum}'
        f'</div>'
    )

    for scope_key, scope_badge in (("domestic", "🇰🇷 국내"), ("global", "🌐 해외")):
        s_data = today_data.get(scope_key) or {}
        kws = s_data.get("keywords") or []
        res_map = s_data.get("results") or {}
        news_map = s_data.get("news") or {}

        for idx, kw in enumerate(kws, 1):
            item = res_map.get(kw) or {}
            arts = news_map.get(kw) or []
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
                if watch
                else ""
            )
            arts_ul = (
                f'<details class="articles-details"><summary>🔗 관련 주요 기사 원문 ({len(arts)}건)</summary><ul class="articles-list">'
                + "".join(
                    f'<li><a href="{_html.escape(str(a.get("link", "#")))}" target="_blank" rel="noopener">'
                    f'{_html.escape(str(a.get("title", "")))}</a></li>'
                    for a in arts
                )
                + "</ul></details>"
                if arts
                else ""
            )
            card_dom_id = f"card-{idx}" if scope_key == "domestic" else f"card-gl-{idx}"
            blocks.append(
                f'<article class="expander-card open" id="{card_dom_id}">'
                f'<button class="expander-header" onclick="toggleExpander(this)">'
                f'<div class="expander-title-wrap"><span class="chevron">❯</span>'
                f'<span>📌 [{scope_badge} {idx}] {headline}</span><span class="kw-badge">{_html.escape(kw)}</span></div></button>'
                f'<div class="expander-body"><h4 class="section-h4">📌 무슨 일이 있었나</h4>'
                f'<ul class="bullet-list">{facts_li}</ul><h4 class="section-h4">💬 핵심 Q&amp;A</h4>'
                f'{qna_div}{watch_ul}{arts_ul}</div></article>'
            )
    return "\n      ".join(blocks)


def update_sitemap(now: datetime | None = None):
    """사이트 전체 정적 페이지(홈, 소개/정책 4종, 계산기 3종, 가이드 9종, 일자별 아카이브 전체)를 sitemap.xml에 기록."""
    if now is None:
        now = datetime.now(KST)
    today_str = f"{now:%Y-%m-%d}"

    urls = [
        ("https://zippy.kr/", today_str, "hourly", "1.0"),
        ("https://zippy.kr/tools/dsr.html", today_str, "weekly", "0.95"),
        ("https://zippy.kr/tools/refinance.html", today_str, "weekly", "0.95"),
        ("https://zippy.kr/tools/jeonse-vs-rent.html", today_str, "weekly", "0.95"),
        ("https://zippy.kr/tools/stock-averaging.html", today_str, "weekly", "0.95"),
        ("https://zippy.kr/tools/compound.html", today_str, "weekly", "0.9"),
        ("https://zippy.kr/tools/us-stock-tax.html", today_str, "weekly", "0.9"),
        ("https://zippy.kr/tools/dividend-tax.html", today_str, "weekly", "0.9"),
        ("https://zippy.kr/tools/pension-tax.html", today_str, "weekly", "0.9"),
        ("https://zippy.kr/guide/index.html", today_str, "weekly", "0.9"),
        ("https://zippy.kr/archive/index.html", today_str, "daily", "0.9"),
    ]

    for g in GUIDES_DATA:
        urls.append((f"https://zippy.kr/guide/{g['slug']}.html", today_str, "monthly", "0.85"))

    if os.path.exists(ARCHIVE_DIR):
        arch_files = sorted(
            [fn for fn in os.listdir(ARCHIVE_DIR) if re.match(r"^\d{4}-\d{2}-\d{2}\.html$", fn)],
            reverse=True,
        )
        for fn in arch_files:
            d_part = fn.replace(".html", "")
            urls.append((f"https://zippy.kr/archive/{fn}", d_part, "monthly", "0.8"))

    for p_file in ("about.html", "privacy.html", "terms.html", "contact.html"):
        urls.append((f"https://zippy.kr/{p_file}", today_str, "monthly", "0.6"))

    xml_items = "\n".join(
        f"""  <url>
    <loc>{loc}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>{freq}</changefreq>
    <priority>{prio}</priority>
  </url>"""
        for loc, lastmod, freq, prio in urls
    )
    xml_full = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{xml_items}
</urlset>
"""
    with open(os.path.join(BASE_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(xml_full)
    return len(urls)


def sync_all_static_pages(output: dict, now: datetime | None = None):
    """batch_generate.py에서 호출되어 index.html, /archive/*, /guide/*, sitemap.xml을 일괄 갱신."""
    if now is None:
        now = datetime.now(KST)

    # 1. 상설 가이드 보장
    build_guides()

    # 2. 오늘/어제 데이터를 아카이브에 반영 및 아카이브 리빌드
    extra = [output.get("yesterday"), output.get("today")]
    rebuild_all_archives([p for p in extra if isinstance(p, dict)])

    # 3. 메인 index.html 프리렌더링 업데이트
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_path):
        try:
            today_data = output.get("today") or {}
            cards_block = _build_main_prerendered_cards(today_data)
            with open(index_path, "r", encoding="utf-8") as f:
                html_text = f.read()
            replacement = f"<!-- PRE_RENDERED_START -->\n      {cards_block}\n      <!-- PRE_RENDERED_END -->"
            html_text = re.sub(
                r"<!-- PRE_RENDERED_START -->.*?<!-- PRE_RENDERED_END -->",
                lambda _: replacement,
                html_text,
                flags=re.DOTALL,
            )
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(html_text)
        except Exception as e:
            print(f"[경고] index.html 프리렌더링 동기화 오류: {e}")

    # 4. 사이트맵 재생성
    url_count = update_sitemap(now)
    print(f"[SEO 빌드 완료] sitemap.xml 총 {url_count}개 정적 URL 등록됨")


if __name__ == "__main__":
    print("[1/3] 상설 경제 가이드 빌드 중...")
    guides = build_guides()
    print(f" -> 가이드 {len(guides)}개 파일 생성 완료")

    print("[2/3] Git 히스토리 기반 일자별 아카이브 백필 중...")
    cnt = backfill_archives_from_git()
    print(f" -> 일자별 아카이브 {cnt}일치 복원 및 HTML 생성 완료")

    print("[3/3] 최신 news_data.json 기준 메인 페이지 & 사이트맵 동기화 중...")
    data_path = os.path.join(BASE_DIR, "news_data.json")
    if os.path.exists(data_path):
        with open(data_path, "r", encoding="utf-8") as f:
            cur_out = json.load(f)
        sync_all_static_pages(cur_out)
