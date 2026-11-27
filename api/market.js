const TICKERS = [
  { key: "kospi", symbol: "^KS11", label: "코스피", unit: "", decimals: 2 },
  { key: "kosdaq", symbol: "^KQ11", label: "코스닥", unit: "", decimals: 2 },
  { key: "nasdaq", symbol: "^IXIC", label: "나스닥", unit: "", decimals: 2 },
  { key: "sp500", symbol: "^GSPC", label: "S&P 500", unit: "", decimals: 2 },
  { key: "usdkrw", symbol: "KRW=X", label: "원/달러", unit: "원", decimals: 1 },
  { key: "us10y", symbol: "^TNX", label: "미 국채 10년", unit: "%", decimals: 2 },
  { key: "vix", symbol: "^VIX", label: "VIX 공포지수", unit: "", decimals: 2 },
  { key: "btc", symbol: "BTC-USD", label: "비트코인", unit: "$", decimals: 0 }
];

async function fetchYahooTicker(spec) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
    spec.symbol
  )}?range=5d&interval=1d`;
  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0 (compatible; ZippyMarketBot/1.0)" }
  });
  if (!res.ok) throw new Error(`Yahoo ${spec.symbol} HTTP ${res.status}`);
  const data = await res.json();
  const result = data?.chart?.result?.[0];
  const meta = result?.meta;
  if (!meta || typeof meta.regularMarketPrice !== "number") {
    throw new Error(`Invalid payload for ${spec.symbol}`);
  }
  const price = Number(meta.regularMarketPrice);

  // Yahoo Finance v8 chart API의 chartPreviousClose는 조회 기간(5d) 시작 전 종가이므로,
  // 실제 '전일 종가'는 일봉 closes 배열과 타임스탬프를 비교해 직전 거래일 종가를 추출해야 정확함.
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const rawCloses = Array.isArray(result?.indicators?.quote?.[0]?.close)
    ? result.indicators.quote[0].close
    : [];
  const validBars = [];
  for (let i = 0; i < rawCloses.length; i++) {
    if (typeof rawCloses[i] === "number" && !Number.isNaN(rawCloses[i])) {
      validBars.push({ ts: timestamps[i] || 0, close: Number(rawCloses[i]) });
    }
  }

  let prevClose = Number(meta.previousClose ?? meta.chartPreviousClose ?? price);
  if (validBars.length >= 2) {
    const gmtoffset = Number(meta.gmtoffset || 0);
    const toLocalDateStr = (unixSec) =>
      new Date((unixSec + gmtoffset) * 1000).toISOString().slice(0, 10);
    const marketDate = meta.regularMarketTime
      ? toLocalDateStr(meta.regularMarketTime)
      : toLocalDateStr(validBars[validBars.length - 1].ts);
    const lastBarDate = toLocalDateStr(validBars[validBars.length - 1].ts);
    prevClose =
      lastBarDate === marketDate
        ? validBars[validBars.length - 2].close
        : validBars[validBars.length - 1].close;
  } else if (validBars.length === 1 && typeof meta.previousClose !== "number") {
    prevClose = validBars[0].close;
  }

  const change = price - prevClose;
  const changePct = prevClose > 0 ? (change / prevClose) * 100 : 0;

  return {
    key: spec.key,
    label: spec.label,
    symbol: spec.symbol,
    unit: spec.unit,
    decimals: spec.decimals,
    price,
    prevClose,
    change,
    changePct,
    high52w: typeof meta.fiftyTwoWeekHigh === "number" ? meta.fiftyTwoWeekHigh : null,
    low52w: typeof meta.fiftyTwoWeekLow === "number" ? meta.fiftyTwoWeekLow : null
  };
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=30");

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  const results = await Promise.allSettled(TICKERS.map((t) => fetchYahooTicker(t)));
  const items = {};
  let successCount = 0;

  results.forEach((r, idx) => {
    const spec = TICKERS[idx];
    if (r.status === "fulfilled") {
      items[spec.key] = r.value;
      successCount += 1;
    }
  });

  return res.status(successCount > 0 ? 200 : 502).json({
    success: successCount > 0,
    updatedAt: new Date().toISOString(),
    source: "vercel-cached-60s",
    items
  });
}
