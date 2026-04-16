export interface MacroMetrics {
  dxy: number | null;
  us10y: number | null;
  vix: number | null;
  btc_etf_flows_usd: number | null;
  sp500: number | null;
  fed_rate_pct: number | null;
}

export interface Candle {
  t: number;
  T: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  q: number;
  n: number;
}
