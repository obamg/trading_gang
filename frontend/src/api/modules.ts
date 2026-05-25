/**
 * API clients for every TradeCore module. Thin wrappers over `http` —
 * page code should import from here instead of calling axios directly.
 */
import { http } from "./client";

// ---------- shared types ----------
export interface Paginated<T> {
  items: T[];
}

// ---------- RadarX ----------
export interface RadarXAlert {
  id: string;
  symbol: string;
  z_score: number;
  ratio: number;
  candle_volume_usd: number;
  avg_volume_usd: number;
  price: number;
  price_change_pct: number | null;
  volume_24h_usd: number | null;
  is_divergence?: boolean;
  divergence_score?: number;
  triggered_at: string;
}
export interface TopMover {
  symbol: string;
  z_score: number;
  ratio: number;
  price: number;
}
export const radarxApi = {
  alerts: (params?: { symbol?: string; hours?: number; limit?: number }) =>
    http.get<Paginated<RadarXAlert>>("/radarx/alerts", { params }).then((r) => r.data),
  topMovers: (limit = 20) =>
    http.get<Paginated<TopMover>>("/radarx/top-movers", { params: { limit } }).then((r) => r.data),
  stats: () => http.get<{ alerts_24h: number; avg_z_score: number; top_symbol: string | null }>("/radarx/stats").then((r) => r.data),
};

// ---------- WhaleRadar ----------
export interface WhaleTrade {
  id: string;
  symbol: string;
  side: string;
  trade_size_usd: number;
  price: number;
  detected_at: string;
}
export interface OnchainTransfer {
  id: string;
  asset: string;
  amount: number;
  amount_usd: number;
  from_label: string | null;
  to_label: string | null;
  transfer_type: string | null;
  chain: string;
  detected_at: string;
}
export interface OISurge {
  id: string;
  symbol: string;
  oi_before_usd: number;
  oi_after_usd: number;
  oi_change_pct: number;
  price: number;
  price_change_pct: number | null;
  direction: string | null;
  detected_at: string;
}
export const whaleApi = {
  trades: (params?: { symbol?: string; hours?: number }) =>
    http.get<Paginated<WhaleTrade>>("/whaleradar/trades", { params }).then((r) => r.data),
  onchain: (params?: { asset?: string; hours?: number }) =>
    http.get<Paginated<OnchainTransfer>>("/whaleradar/onchain", { params }).then((r) => r.data),
  oiSurges: (params?: { symbol?: string; hours?: number }) =>
    http.get<Paginated<OISurge>>("/whaleradar/oi-surges", { params }).then((r) => r.data),
};

// ---------- LiquidMap ----------
export interface HeatmapLevel {
  side: "long" | "short";
  price: number;
  size_usd: number;
}
export interface LiquidationEvent {
  id: string;
  symbol: string;
  side: string;
  size_usd: number;
  price: number;
  detected_at: string;
}
export const liquidApi = {
  heatmap: (symbol: string, limit = 20) =>
    http.get<{ symbol: string; levels: HeatmapLevel[] }>(`/liquidmap/heatmap/${symbol}`, { params: { limit } }).then((r) => r.data),
  recent: (params?: { symbol?: string; hours?: number }) =>
    http.get<Paginated<LiquidationEvent>>("/liquidmap/recent", { params }).then((r) => r.data),
  stats: (symbol: string) =>
    http.get<{ symbol: string; long_usd_24h: number; short_usd_24h: number; net_usd_24h: number }>(`/liquidmap/stats/${symbol}`).then((r) => r.data),
};

// ---------- SentimentPulse ----------
export interface FundingRow {
  symbol: string;
  funding_rate: number | null;
  long_ratio: number | null;
  short_ratio: number | null;
  snapshot_at: string;
}
export const sentimentApi = {
  overview: () => http.get<{ fear_greed_index: number | null; fear_greed_label: string | null; btc_dominance_pct: number | null; total_mcap_usd: number | null; snapshot_at: string | null }>("/sentiment/overview").then((r) => r.data),
  funding: (limit = 30) => http.get<Paginated<FundingRow>>("/sentiment/funding", { params: { limit } }).then((r) => r.data),
  longShort: (limit = 30) => http.get<Paginated<FundingRow>>("/sentiment/long-short", { params: { limit } }).then((r) => r.data),
  history: (symbol: string, hours = 168) =>
    http.get<Paginated<FundingRow>>(`/sentiment/history/${symbol}`, { params: { hours } }).then((r) => r.data),
};

// ---------- MacroPulse ----------
export interface MacroSnapshot {
  dxy: number | null;
  us10y: number | null;
  us2y: number | null;
  vix: number | null;
  sp500: number | null;
  nasdaq: number | null;
  gold: number | null;
  btc_etf_flows_usd: number | null;
  macro_score: number | null;
  snapshot_at: string | null;
}
export interface EconEvent {
  id: string;
  name: string;
  country: string | null;
  impact: string | null;
  scheduled_at: string;
  actual: string | null;
  forecast: string | null;
  previous: string | null;
}
export const macroApi = {
  snapshot: () => http.get<MacroSnapshot>("/macro/snapshot").then((r) => r.data),
  score: (symbol?: string) => http.get<{ macro_score: number; dxy_trend: string; vix_level: string; etf_flows: string; risk_environment: string; key_events_24h: string[] }>("/macro/score", { params: { symbol } }).then((r) => r.data),
  calendar: (hours = 72) => http.get<Paginated<EconEvent>>("/macro/calendar", { params: { hours } }).then((r) => r.data),
  etfFlows: () => http.get<{ flow_usd: number | null; snapshot_at: string | null }>("/macro/etf-flows").then((r) => r.data),
  history: (days = 30) => http.get<Paginated<MacroSnapshot>>("/macro/history", { params: { days } }).then((r) => r.data),
};

// ---------- GemRadar ----------
export interface GemAlert {
  id: string;
  symbol: string;
  name: string | null;
  chain: string | null;
  contract_address: string | null;
  price_usd: number | null;
  market_cap_usd: number | null;
  liquidity_usd: number | null;
  volume_24h_usd: number | null;
  volume_usd_current: number | null;
  price_change_5m: number | null;
  price_change_1h: number | null;
  price_change_24h: number | null;
  risk_score_numeric: number | null;
  risk_label: string | null;
  risk_flags: string[] | null;
  dex: string | null;
  detected_at: string;
}
export const gemApi = {
  alerts: (params?: { risk?: string; limit?: number }) =>
    http.get<Paginated<GemAlert>>("/gemradar/alerts", { params }).then((r) => r.data),
  trending: () => http.get<Paginated<GemAlert>>("/gemradar/trending").then((r) => r.data),
  newListings: () => http.get<Paginated<{ symbol: string; exchange: string; listed_at: string }>>("/gemradar/new-listings").then((r) => r.data),
};

// ---------- RiskCalc ----------
export interface CalcResult {
  id: string;
  risk_amount_usd: number;
  stop_distance_pct: number;
  position_size_units: number;
  position_size_usd: number;
  leverage: number;
  liquidation_price: number | null;
  max_loss_usd: number;
  potential_profit_usd: number | null;
  rr_ratio: number | null;
  warnings: string[];
}
export interface CalcInput {
  account_balance_usd: number;
  risk_pct: number;
  entry_price: number;
  stop_loss_price: number;
  take_profit_price?: number;
  max_leverage?: number;
  asset_type?: string;
  side?: string;
  symbol?: string;
  oracle_signal_id?: string;
}
export interface CalcHistoryRow extends CalcResult {
  symbol: string | null;
  account_balance_usd: number;
  risk_pct: number;
  entry_price: number;
  stop_loss_price: number;
  take_profit_price: number | null;
  calculated_at: string;
}
export const riskcalcApi = {
  calculate: (body: CalcInput) => http.post<CalcResult>("/riskcalc/calculate", body).then((r) => r.data),
  history: (limit = 20) => http.get<Paginated<CalcHistoryRow>>("/riskcalc/history", { params: { limit } }).then((r) => r.data),
};

// ---------- Performance ----------
export interface PerfSnap {
  period: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number | null;
  expectancy: number | null;
  profit_factor: number | null;
  total_pnl_usd: number | null;
  net_pnl_usd: number | null;
  max_drawdown_usd: number | null;
  max_drawdown_pct: number | null;
  best_trade_pnl_usd: number | null;
  worst_trade_pnl_usd: number | null;
  best_setup: string | null;
  avg_rr_achieved: number | null;
}
export const performanceApi = {
  overview: (is_paper = false) =>
    http.get<Record<string, PerfSnap>>("/performance/overview", { params: { is_paper } }).then((r) => r.data),
  equityCurve: (days = 90, is_paper = false) =>
    http.get<{ points: { t: string; equity: number }[] }>("/performance/equity-curve", { params: { days, is_paper } }).then((r) => r.data),
  bySetup: () => http.get<Paginated<{ setup: string; total_trades: number; win_rate: number | null; avg_pnl_pct: number | null; avg_r_multiple: number | null; net_pnl_usd: number | null }>>("/performance/by-setup").then((r) => r.data),
  bySymbol: (limit = 20) => http.get<Paginated<{ symbol: string; total_trades: number; net_pnl_usd: number }>>("/performance/by-symbol", { params: { limit } }).then((r) => r.data),
  byTime: () => http.get<{ by_hour: { hour: number; trades: number; net_pnl_usd: number }[]; by_day_of_week: { day_of_week: number; trades: number; net_pnl_usd: number }[] }>("/performance/by-time").then((r) => r.data),
  signals: (module = "oracle") => http.get<{ module: string; total_signals: number; accuracy_1h_pct: number | null; accuracy_4h_pct: number | null; avg_move_1h_pct: number | null; avg_move_4h_pct: number | null }>("/performance/signals", { params: { module } }).then((r) => r.data),
  rDist: () => http.get<{ buckets: { range: string; count: number }[]; total: number }>("/performance/r-distribution").then((r) => r.data),
};

// ---------- Oracle ----------
export interface OracleSignal {
  id: string;
  symbol: string;
  score: number;
  recommendation: string;
  confidence: string;
  confluence_count: number;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  rr_ratio: number | null;
  macro_score: number | null;
  is_paper: boolean;
  signal_at: string;
  signals_breakdown?: Record<string, { direction: string; intensity: number; weight: number; contribution: number; detail: unknown }>;
  outcome?: {
    price_at_signal: number;
    price_15m: number | null;
    price_1h: number | null;
    price_4h: number | null;
    pnl_1h_pct: number | null;
    pnl_4h_pct: number | null;
    was_correct_1h: boolean | null;
    was_correct_4h: boolean | null;
  } | null;
}
export interface LiveOracle {
  symbol: string;
  score: number;
  recommendation: string;
  confidence: string;
  confluence_count: number;
  signals_breakdown: Record<string, { direction: string; intensity: number; weight: number; contribution: number }>;
  current_price: number;
  macro_context: { macro_score: number | null; vix_level: string | null; dxy_trend: string | null; risk_environment: string | null };
}
export const oracleApi = {
  signals: (params?: { symbol?: string; recommendation?: string; min_score?: number; limit?: number; offset?: number }) =>
    http.get<Paginated<OracleSignal>>("/oracle/signals", { params }).then((r) => r.data),
  detail: (id: string) => http.get<OracleSignal>(`/oracle/signals/${id}`).then((r) => r.data),
  live: (symbol: string) => http.get<LiveOracle>(`/oracle/live/${symbol}`).then((r) => r.data),
  generate: (symbol: string, persist = true) => http.post<OracleSignal>("/oracle/generate", { symbol, persist }).then((r) => r.data),
  performance: () => http.get<{ total_signals: number; measured_1h: number; measured_4h: number; accuracy_1h_pct: number | null; accuracy_4h_pct: number | null }>("/oracle/performance").then((r) => r.data),
  updateSettings: (body: Record<string, unknown>) => http.post("/oracle/settings", body).then((r) => r.data),
  board: (params?: { direction?: "bullish" | "bearish"; min_modules?: number; min_score?: number }) =>
    http.get<OracleBoardResponse>("/oracle/board", { params }).then((r) => r.data),
};
export interface OracleBoardModule {
  name: string;
  direction: string;
  intensity: number;
  weight?: number;
  contribution?: number;
}
export interface OracleBoardRow {
  symbol: string;
  score: number;
  recommendation: string;
  confidence: string;
  confluence_count: number;
  agreeing_count: number;
  current_price: number | null;
  modules: OracleBoardModule[];
}
export interface OracleBoardResponse {
  bullish: OracleBoardRow[];
  bearish: OracleBoardRow[];
  updated_at: string | null;
  stale: boolean;
  min_modules: number;
  universe_size?: number;
}

// ---------- FlowPulse ----------
export interface FlowSignalRow {
  id: string;
  symbol: string;
  bid_usd: number | null;
  ask_usd: number | null;
  book_imbalance: number | null;
  taker_buy_vol: number | null;
  taker_sell_vol: number | null;
  taker_ratio: number | null;
  top_long_ratio: number | null;
  top_short_ratio: number | null;
  direction: string | null;
  intensity: number | null;
  window?: number;
  snapshot_at: string;
}
export const flowApi = {
  overview: () =>
    http.get<{ items: FlowSignalRow[]; snapshot_at: string | null }>("/flowpulse/overview").then((r) => r.data),
  live: (symbol: string) =>
    http.get<{ symbol: string; signal: Record<string, unknown> | null }>(`/flowpulse/live/${symbol}`).then((r) => r.data),
  extremes: (hours = 1) =>
    http.get<Paginated<FlowSignalRow>>("/flowpulse/extremes", { params: { hours } }).then((r) => r.data),
  history: (symbol: string, hours = 24) =>
    http.get<{ symbol: string; items: FlowSignalRow[] }>(`/flowpulse/history/${symbol}`, { params: { hours } }).then((r) => r.data),
  stats: () =>
    http.get<{ snapshots_24h: number; bullish_24h: number; bearish_24h: number; avg_book_imbalance: number | null; avg_taker_ratio: number | null }>("/flowpulse/stats").then((r) => r.data),
};

// ---------- WalletWatch (discovery leaderboard) ----------
export interface DiscoveryRow {
  wallet_address: string;
  chain: string;
  total_realized_usd: number;
  total_unrealized_usd: number;
  total_cost_basis_usd: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_multiple: number;
  best_multiple: number;
  token_count: number;
  discovery_score: number;
  promoted_at: string | null;
  last_scored_at: string;
}
export interface DiscoveryWalletDetail {
  wallet_address: string;
  score: {
    discovery_score: number;
    total_realized_usd: number;
    total_unrealized_usd: number;
    win_count: number;
    loss_count: number;
    win_rate: number;
    best_multiple: number;
    token_count: number;
    promoted_at: string | null;
  } | null;
  tokens: Array<{
    chain: string;
    token_address: string;
    token_symbol: string | null;
    total_buy_usd: number;
    total_sell_usd: number;
    current_value_usd: number;
    realized_pnl_usd: number;
    unrealized_pnl_usd: number;
    multiple: number | null;
    first_buy_at: string | null;
  }>;
}
export interface DiscoveryToken {
  chain: string;
  address: string;
  symbol: string | null;
  name: string | null;
  source: string;
  discovered_at: string;
  last_scored_at: string | null;
}
export const walletwatchApi = {
  leaderboard: (params?: {
    chain?: string;
    min_realized?: number;
    min_win_rate?: number;
    min_token_count?: number;
    only_unpromoted?: boolean;
    limit?: number;
    offset?: number;
  }) =>
    http
      .get<Paginated<DiscoveryRow>>("/walletwatch/discovery/leaderboard", { params })
      .then((r) => r.data),
  walletDetail: (address: string) =>
    http
      .get<DiscoveryWalletDetail>(`/walletwatch/discovery/wallet/${address}`)
      .then((r) => r.data),
  tokens: (params?: { chain?: string; limit?: number }) =>
    http
      .get<Paginated<DiscoveryToken>>("/walletwatch/discovery/tokens", { params })
      .then((r) => r.data),
  promote: (address: string, name: string, entity_type = "smart_money") =>
    http
      .post<{ ok: boolean; entity_id?: string; reason?: string }>(
        `/walletwatch/discovery/promote/${address}`,
        null,
        { params: { name, entity_type } },
      )
      .then((r) => r.data),
};

// ---------- NewsPulse ----------
export interface NewsArticle {
  id: string;
  title: string;
  url: string;
  source: string;
  sentiment: string | null;
  importance: string | null;
  coins: string[];
  published_at: string;
}
export const newsApi = {
  articles: (params?: { limit?: number; sentiment?: string; importance?: string; coin?: string }) =>
    http.get<Paginated<NewsArticle>>("/news/articles", { params }).then((r) => r.data),
  stats: () => http.get<{ articles_24h: number; bullish_24h: number; bearish_24h: number; high_impact_24h: number }>("/news/stats").then((r) => r.data),
};

// ---------- ListingWatch ----------
export interface ListingExchangeRef {
  exchange: string;
  market_type: string;
  symbol: string;
}
export interface ListingEvent {
  id: string;
  exchange: string;
  market_type: string;
  symbol: string;
  base_asset: string;
  quote_asset: string;
  is_cross_listing: boolean;
  other_exchanges: ListingExchangeRef[] | null;
  innovation: boolean;
  detected_at: string;
  listed_at: string | null;
  watcher_ends_at: string;
  t0_price: number | null;
  last_price: number | null;
  high_15m: number | null;
  low_15m: number | null;
  high_1h: number | null;
  low_1h: number | null;
  last_funding_pct: number | null;
  signal_count: number;
  status: string;
}
export interface ListingSignal {
  id: string;
  listing_id: string;
  signal_type: string;
  direction: string;
  conviction: number;
  price_at_emit: number | null;
  seconds_since_t0: number | null;
  context: Record<string, unknown> | null;
  emitted_at: string;
}
export const listingApi = {
  active: () => http.get<{ items: ListingEvent[] }>("/listings/active").then((r) => r.data),
  recent: (days = 7) =>
    http.get<{ items: ListingEvent[] }>("/listings/recent", { params: { days } }).then((r) => r.data),
  detail: (id: string) =>
    http
      .get<{ event: ListingEvent; signals: ListingSignal[] }>(`/listings/${id}`)
      .then((r) => r.data),
};
