import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { MajorsBotAnalytics, MajorsBotStatus, MajorsBotTrade } from "@/api/modules";
import MajorsBotPage from "..";

const statusFixture: MajorsBotStatus = {
  enabled: true,
  paper_equity: 10_000,
  open_positions: 1,
  pending_orders: 2,
  concurrent_count: 1,
  max_concurrent: 6,
  config: {
    symbols: [
      "BTCUSDT",
      "ETHUSDT",
      "SOLUSDT",
      "BNBUSDT",
      "XRPUSDT",
      "DOGEUSDT",
      "ADAUSDT",
      "AVAXUSDT",
      "LINKUSDT",
      "LTCUSDT",
    ],
    volevent_enabled: true,
    fundingfade_enabled: false,
    paper_equity_initial: 10_000,
    risk_per_trade_pct: 0.0025,
    position_size_pct: 0.05,
    maker_fee_pct: 0.0002,
    taker_fee_pct: 0.0006,
    slippage_pct: 0.0002,
    max_hold_hours: 168,
  },
};

const tradeFixture: MajorsBotTrade = {
  id: "t-1",
  symbol: "BTCUSDT",
  exchange: "bybit",
  market_type: "perp",
  direction: "long",
  strategy: "volevent",
  signal_at: "2026-07-20T10:00:00+00:00",
  entry_price: 65000,
  entry_at: "2026-07-20T10:05:00+00:00",
  entry_bar_at: "2026-07-20T10:00:00+00:00",
  entry_mode: "limit",
  limit_price: 65000,
  expire_at: null,
  signal_high: 66000,
  signal_low: 64000,
  notional_usd: 500,
  qty: 0.0077,
  paper_equity_at_entry: 10_000,
  stop_price: 64000,
  initial_stop_price: 64000,
  take_profit_price: 67000,
  peak_price: null,
  partial_exit_price: null,
  partial_exit_at: null,
  partial_pnl_usd: null,
  partial_qty: null,
  close_price: 66500,
  closed_at: "2026-07-21T02:00:00+00:00",
  close_reason: "trail_stop",
  realized_pnl_usd: 11.55,
  realized_r: 1.5,
  realized_r_net: 1.42,
  fees_usd: 0.4,
  funding_pnl_usd: -0.1,
  funding_rate_at_entry: 0.0001,
  funding_pctile_at_entry: 55,
  status: "closed",
};

const analyticsFixture: MajorsBotAnalytics = {
  days: 90,
  by_strategy: [
    {
      label: "volevent",
      n_trades: 12,
      wins: 7,
      win_rate: 0.583,
      realized_pnl_usd: 120.5,
      realized_r: 4.1,
      realized_r_net: 3.2,
      avg_r_net: 0.267,
      expectancy_r_net: 0.267,
      fees_usd: 12.3,
      funding_pnl_usd: -3.1,
    },
    {
      label: "fundingfade",
      n_trades: 40,
      wins: 25,
      win_rate: 0.625,
      realized_pnl_usd: 80.2,
      realized_r: 2.5,
      realized_r_net: 1.9,
      avg_r_net: 0.048,
      expectancy_r_net: 0.048,
      fees_usd: 30.5,
      funding_pnl_usd: 9.4,
    },
  ],
  by_strategy_direction: [
    {
      label: "volevent/long",
      n_trades: 8,
      wins: 5,
      win_rate: 0.625,
      realized_pnl_usd: 90.1,
      realized_r: 3.0,
      realized_r_net: 2.5,
      avg_r_net: 0.313,
      expectancy_r_net: 0.313,
      fees_usd: 8.0,
      funding_pnl_usd: -2.0,
    },
  ],
  by_symbol: [
    {
      label: "BTCUSDT",
      n_trades: 6,
      wins: 4,
      win_rate: 0.667,
      realized_pnl_usd: 60.0,
      realized_r: 2.0,
      realized_r_net: 1.7,
      avg_r_net: 0.283,
      expectancy_r_net: 0.283,
      fees_usd: 5.0,
      funding_pnl_usd: 1.0,
    },
  ],
  paper_equity: 10_000,
};

vi.mock("@/api/modules", () => ({
  majorsbotApi: {
    status: vi.fn(() => Promise.resolve(statusFixture)),
    trades: vi.fn(() => Promise.resolve({ items: [tradeFixture], limit: 200, offset: 0 })),
    analytics: vi.fn(() => Promise.resolve(analyticsFixture)),
  },
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MajorsBotPage />
    </QueryClientProvider>,
  );
}

describe("<MajorsBotPage>", () => {
  it("renders the header and status cards", async () => {
    renderPage();
    expect(screen.getByText("MajorsBot")).toBeInTheDocument();
    expect(await screen.findByText("Paper Equity")).toBeInTheDocument();
    expect(screen.getByText("Pending Orders")).toBeInTheDocument();
  });

  it("shows the per-strategy split with verdict gates", async () => {
    renderPage();
    // Strategy badges for both strategies render once analytics resolves.
    expect((await screen.findAllByText("volevent")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("fundingfade").length).toBeGreaterThan(0);
    // Verdict gates: volevent n>=30, fundingfade n>=100.
    expect(await screen.findByText("12 / 30 closed")).toBeInTheDocument();
    expect(await screen.findByText("40 / 100 closed")).toBeInTheDocument();
  });

  it("renders trades from the trades endpoint", async () => {
    renderPage();
    expect((await screen.findAllByText("BTCUSDT")).length).toBeGreaterThan(0);
  });
});
