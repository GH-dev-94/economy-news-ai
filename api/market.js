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
  const meta = data?.chart?.result?.[0]?.meta;
  if (!meta || typeof meta.regularMarketPrice !== "number") {
    throw new Error(`Invalid payload for ${spec.symbol}`);
  }
  const price = Number(meta.regularMarketPrice);
  const prevClose = Number(meta.chartPreviousClose ?? meta.previousClose ?? price);
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
