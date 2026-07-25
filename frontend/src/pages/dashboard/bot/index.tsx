import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowDown, ArrowUp, Power } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { MetricCard } from "@/components/ui/MetricCard";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { Tabs } from "@/components/ui/Tabs";
import {
  botApi,
  type BotTrade,
  type BotSkippedSignal,
  type BotEquityPoint,
  type BotAnalyticsRow,
} from "@/api/modules";

const SKIP_REASONS = [
  "",
  "bot_disabled",
  "kill_switch",
  "already_open",
  "cooldown",
  "max_concurrent",
  "oracle_veto",
  "news_veto",
  "no_candles",
  "no_equity",
  "invalid_direction",
  "exchange_unsupported",
  "not_perp",
  "symbol_blocked",
  "low_turnover",
  "direction_disabled",
  "max_open_risk",
  "low_vol_ratio",
  "funding_extreme",
  "blocked_hour",
] as const;

type TabKey = "open" | "pending" | "closed" | "skipped" | "equity" | "analytics";

export default function WaveBotPage() {
  const [tab, setTab] = useState<TabKey>("open");

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["bot", "status"],
    queryFn: botApi.status,
    refetchInterval: 15_000,
  });

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">WaveBot</h1>
          <p className="text-sm text-textSecondary">
            Paper trading on <span className="text-textPrimary">wave_active</span> signals.
            v2: retrace limit entries, partial at 1.5R, trailed runner.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {status?.enabled ? <LiveIndicator /> : <PausedChip />}
          {status?.kill_switch_tripped ? <KillSwitchChip /> : null}
        </div>
      </header>

      {statusLoading || !status ? (
        <Skeleton className="h-24" />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricCard
            label="Paper Equity"
            value={status.paper_equity}
            valueDecimals={2}
            valuePrefix="$"
          />
          <MetricCard
            label="Today's Drawdown"
            value={status.drawdown_pct * 100}
            valueDecimals={2}
            valueSuffix="%"
          />
          <MetricCard
            label="Open Positions"
            value={status.concurrent_open}
            valueDecimals={0}
            valueSuffix={` / ${status.max_concurrent}`}
          />
          <MetricCard
            label="Anchor (00:00 UTC)"
            value={status.daily_anchor}
            valueDecimals={2}
            valuePrefix="$"
          />
        </div>
      )}

      <Card>
        <CardHeader>
          <Tabs
            tabs={[
              { key: "open", label: "Open positions" },
              { key: "pending", label: "Pending orders" },
              { key: "closed", label: "History" },
              { key: "skipped", label: "Skipped signals" },
              { key: "equity", label: "Equity curve" },
              { key: "analytics", label: "Analytics" },
            ]}
            active={tab}
            onChange={(k) => setTab(k as TabKey)}
          />
        </CardHeader>
        <CardBody className="p-0">
          {tab === "open" && <OpenPositionsTab />}
          {tab === "pending" && <PendingOrdersTab />}
          {tab === "closed" && <ClosedTradesTab />}
          {tab === "skipped" && <SkippedTab />}
          {tab === "equity" && <EquityCurveTab />}
          {tab === "analytics" && <AnalyticsTab />}
        </CardBody>
      </Card>
    </div>
  );
}

// ---------- chips ----------

function PausedChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-bgSecondary px-2 py-0.5 text-xs text-textMuted border border-borderSubtle">
      <Power size={12} /> Paused
    </span>
  );
}

function KillSwitchChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-loss/10 px-2 py-0.5 text-xs text-loss border border-loss/30">
      <AlertTriangle size={12} /> Kill switch tripped
    </span>
  );
}

// ---------- open positions tab ----------

function OpenPositionsTab() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["bot", "positions"],
    queryFn: botApi.positions,
    refetchInterval: 10_000,
  });
  const closeMutation = useMutation({
    mutationFn: (id: string) => botApi.closeManual(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bot", "positions"] });
      queryClient.invalidateQueries({ queryKey: ["bot", "status"] });
      queryClient.invalidateQueries({ queryKey: ["bot", "trades"] });
    },
  });

  if (isLoading) return <Skeleton className="m-4 h-48" />;
  const rows = data?.items ?? [];

  const cols: Column<BotTrade>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <SymbolCell symbol={r.symbol} exchange={r.exchange} /> },
    { key: "dir", header: "Side", accessor: (r) => <DirectionBadge dir={r.direction} /> },
    {
      key: "entry",
      header: "Entry",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.entry_price} decimals={5} />,
    },
    {
      key: "stop",
      header: "Stop",
      align: "right",
      accessor: (r) => <StopCell trade={r} />,
    },
    {
      key: "tp",
      header: "Take profit",
      align: "right",
      accessor: (r) => (
        <span className="inline-flex items-center justify-end gap-1">
          {r.partial_exit_at ? <PartialChip trade={r} /> : null}
          <span className="text-bullish">
            <NumberDisplay value={r.take_profit_price} decimals={5} />
          </span>
        </span>
      ),
    },
    {
      key: "notional",
      header: "Notional",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.notional_usd} decimals={2} prefix="$" />,
    },
    {
      key: "opened",
      header: "Opened",
      accessor: (r) => <RelTime iso={r.entry_at} />,
    },
    {
      key: "actions",
      header: "",
      accessor: (r) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => closeMutation.mutate(r.id)}
          disabled={closeMutation.isPending}
        >
          Close
        </Button>
      ),
    },
  ];
  return <Table columns={cols} rows={rows} rowKey={(r) => r.id} emptyMessage="No open positions." />;
}

// ---------- pending orders tab (v2 retrace limits) ----------

function PendingOrdersTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["bot", "trades", { status: "pending" }],
    queryFn: () => botApi.trades({ status: "pending", limit: 100 }),
    refetchInterval: 10_000,
  });
  // Guard on the row status: only actual pending limits belong here.
  const rows = (data?.items ?? []).filter((t) => t.status === "pending");

  if (isLoading) return <Skeleton className="m-4 h-48" />;

  const cols: Column<BotTrade>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <SymbolCell symbol={r.symbol} exchange={r.exchange} /> },
    { key: "dir", header: "Side", accessor: (r) => <DirectionBadge dir={r.direction} /> },
    {
      key: "mode",
      header: "Entry",
      accessor: (r) => <Badge variant="neutral">{r.entry_mode ?? "retrace"}</Badge>,
    },
    {
      key: "limit",
      header: "Limit price",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.limit_price ?? r.entry_price} decimals={5} />,
    },
    {
      key: "stop",
      header: "Stop",
      align: "right",
      accessor: (r) => (
        <span className="text-loss">
          <NumberDisplay value={r.stop_price} decimals={5} />
        </span>
      ),
    },
    {
      key: "notional",
      header: "Notional",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.notional_usd} decimals={2} prefix="$" />,
    },
    {
      key: "placed",
      header: "Placed",
      accessor: (r) => <RelTime iso={r.entry_at} />,
    },
    {
      key: "expires",
      header: "Expires",
      accessor: (r) => <RelTime iso={r.expire_at} />,
    },
  ];
  return (
    <Table
      columns={cols}
      rows={rows}
      rowKey={(r) => r.id}
      emptyMessage="No pending limit orders."
    />
  );
}

// ---------- closed trades tab ----------

function ClosedTradesTab() {
  const [symbol, setSymbol] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<"closed" | "cancelled" | "all">("closed");
  const { data, isLoading } = useQuery({
    queryKey: ["bot", "trades", { symbol, reason, status }],
    queryFn: () =>
      botApi.trades({
        symbol: symbol || undefined,
        reason: reason || undefined,
        status,
        limit: 200,
      }),
    refetchInterval: 30_000,
  });
  // Guard on row status so a backend that ignores the param can't leak
  // closed rows into the cancelled view.
  const rows = useMemo(
    () =>
      (data?.items ?? []).filter((t) =>
        status === "all" ? t.status === "closed" || t.status === "cancelled" : t.status === status,
      ),
    [data, status],
  );

  const closedRows = useMemo(() => rows.filter((r) => r.status === "closed"), [rows]);
  const totalPnl = useMemo(
    () => closedRows.reduce((s, r) => s + (r.realized_pnl_usd ?? 0), 0),
    [closedRows],
  );
  const wins = closedRows.filter((r) => (r.realized_pnl_usd ?? 0) > 0).length;
  const winRate = closedRows.length > 0 ? wins / closedRows.length : 0;

  const cols: Column<BotTrade>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <SymbolCell symbol={r.symbol} exchange={r.exchange} /> },
    { key: "dir", header: "Side", accessor: (r) => <DirectionBadge dir={r.direction} /> },
    {
      key: "entry",
      header: "Entry",
      align: "right",
      accessor: (r) => (
        <span className="inline-flex items-center justify-end gap-1">
          {r.partial_exit_at ? <PartialChip trade={r} /> : null}
          <NumberDisplay value={r.entry_price} decimals={5} />
        </span>
      ),
    },
    {
      key: "exit",
      header: "Exit",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.close_price} decimals={5} />,
    },
    {
      key: "reason",
      header: "Reason",
      accessor: (r) => <CloseReasonBadge reason={r.close_reason} />,
    },
    {
      key: "pnl",
      header: "P&L",
      align: "right",
      accessor: (r) =>
        r.realized_pnl_usd === null ? (
          <span className="text-textMuted">—</span>
        ) : (
          <span className={r.realized_pnl_usd >= 0 ? "text-bullish" : "text-loss"}>
            <NumberDisplay value={r.realized_pnl_usd} decimals={2} prefix="$" />
          </span>
        ),
    },
    {
      key: "r",
      header: "R",
      align: "right",
      accessor: (r) =>
        r.realized_r === null ? (
          <span className="text-textMuted">—</span>
        ) : (
          <span className={r.realized_r >= 0 ? "text-bullish" : "text-loss"}>
            {r.realized_r.toFixed(2)}R
          </span>
        ),
    },
    {
      key: "rnet",
      header: "Net R",
      align: "right",
      accessor: (r) =>
        r.realized_r_net === null ? (
          <span className="text-textMuted">—</span>
        ) : (
          <span className={r.realized_r_net >= 0 ? "text-bullish" : "text-loss"}>
            {r.realized_r_net.toFixed(2)}R
          </span>
        ),
    },
    {
      key: "closed",
      header: "Closed",
      accessor: (r) => <RelTime iso={r.closed_at} />,
    },
  ];

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 border-b border-borderSubtle px-4 py-3 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-textMuted">Filter:</span>
          <Select
            value={status}
            onChange={(e) => setStatus(e.target.value as "closed" | "cancelled" | "all")}
          >
            <option value="closed">closed</option>
            <option value="cancelled">cancelled</option>
            <option value="all">all</option>
          </Select>
          <input
            placeholder="symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="w-32 rounded border border-borderSubtle bg-bgSecondary px-2 py-1 text-xs"
          />
          <Select value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">all reasons</option>
            <option value="stop">stop</option>
            <option value="tp">tp</option>
            <option value="manual">manual</option>
            <option value="kill_switch">kill_switch</option>
            <option value="max_hold">max_hold</option>
            <option value="expired">expired</option>
          </Select>
        </div>
        <div className="ml-auto flex gap-4">
          <span>
            <span className="text-textMuted">Σ P&L: </span>
            <span className={totalPnl >= 0 ? "text-bullish" : "text-loss"}>
              <NumberDisplay value={totalPnl} decimals={2} prefix="$" />
            </span>
          </span>
          <span>
            <span className="text-textMuted">Win rate: </span>
            <span className="text-textPrimary">{(winRate * 100).toFixed(0)}%</span>
            <span className="text-textMuted text-xs"> ({wins}/{closedRows.length})</span>
          </span>
        </div>
      </div>
      {isLoading ? (
        <Skeleton className="m-4 h-48" />
      ) : (
        <Table
          columns={cols}
          rows={rows}
          rowKey={(r) => r.id}
          emptyMessage={
            status === "cancelled" ? "No cancelled orders yet." : "No closed trades yet."
          }
        />
      )}
    </div>
  );
}

// ---------- skipped tab ----------

function SkippedTab() {
  const [reason, setReason] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["bot", "skipped", { reason }],
    queryFn: () => botApi.skipped({ reason: reason || undefined, limit: 200 }),
    refetchInterval: 30_000,
  });
  const rows = data?.items ?? [];

  const reasonCounts = useMemo(() => {
    const acc: Record<string, number> = {};
    for (const r of rows) acc[r.skip_reason] = (acc[r.skip_reason] ?? 0) + 1;
    return acc;
  }, [rows]);

  const cols: Column<BotSkippedSignal>[] = [
    { key: "when", header: "When", accessor: (r) => <RelTime iso={r.alert_detected_at} /> },
    { key: "symbol", header: "Symbol", accessor: (r) => <SymbolCell symbol={r.symbol} exchange={r.exchange} /> },
    { key: "dir", header: "Intended", accessor: (r) => <DirectionBadge dir={r.direction} /> },
    {
      key: "reason",
      header: "Skip reason",
      accessor: (r) => <Badge variant="neutral">{r.skip_reason}</Badge>,
    },
    {
      key: "oracle",
      header: "Oracle",
      align: "right",
      accessor: (r) =>
        r.oracle_score !== null ? (
          <span className={r.oracle_score >= 0 ? "text-bullish" : "text-loss"}>
            {r.oracle_score.toFixed(0)}
          </span>
        ) : (
          <span className="text-textMuted">—</span>
        ),
    },
  ];

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-borderSubtle px-4 py-3 text-xs">
        <Select value={reason} onChange={(e) => setReason(e.target.value)}>
          {SKIP_REASONS.map((r) => (
            <option key={r} value={r}>{r || "all reasons"}</option>
          ))}
        </Select>
        <div className="ml-auto flex flex-wrap gap-1 text-textMuted">
          {Object.entries(reasonCounts).map(([k, v]) => (
            <span key={k} className="rounded bg-bgSecondary px-2 py-0.5">
              {k}: <span className="text-textPrimary">{v}</span>
            </span>
          ))}
        </div>
      </div>
      {isLoading ? (
        <Skeleton className="m-4 h-48" />
      ) : (
        <Table columns={cols} rows={rows} rowKey={(r) => r.id} emptyMessage="No skipped signals." />
      )}
    </div>
  );
}

// ---------- equity curve tab ----------

function EquityCurveTab() {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useQuery({
    queryKey: ["bot", "equity-curve", days],
    queryFn: () => botApi.equityCurve(days),
    refetchInterval: 60_000,
  });
  const points = data?.items ?? [];

  return (
    <div className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm">
          <span className="text-textMuted">Current equity: </span>
          <span className="text-textPrimary">
            <NumberDisplay value={data?.current_equity ?? 0} decimals={2} prefix="$" />
          </span>
        </div>
        <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
          <option value="7">7d</option>
          <option value="30">30d</option>
          <option value="90">90d</option>
          <option value="365">1y</option>
        </Select>
      </div>
      {isLoading ? (
        <Skeleton className="h-48" />
      ) : points.length === 0 ? (
        <div className="py-12 text-center text-sm text-textMuted">
          No closed trades yet — chart will populate after the bot's first close.
        </div>
      ) : (
        <EquitySparkline points={points} />
      )}
    </div>
  );
}

function EquitySparkline({ points }: { points: BotEquityPoint[] }) {
  const cum: { day: string; equity: number; pnl: number }[] = [];
  let running = 0;
  for (const p of points) {
    running += p.realized_pnl_usd;
    cum.push({ day: p.day ?? "", equity: running, pnl: p.realized_pnl_usd });
  }
  const min = Math.min(0, ...cum.map((c) => c.equity));
  const max = Math.max(0, ...cum.map((c) => c.equity));
  const w = 800;
  const h = 200;
  const pad = 24;
  const xstep = cum.length > 1 ? (w - 2 * pad) / (cum.length - 1) : 0;
  const yscale = (v: number) =>
    h - pad - ((v - min) / (max - min || 1)) * (h - 2 * pad);
  const path = cum
    .map((c, i) => `${i === 0 ? "M" : "L"} ${pad + i * xstep} ${yscale(c.equity)}`)
    .join(" ");
  const zeroY = yscale(0);
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-48">
        <line x1={pad} y1={zeroY} x2={w - pad} y2={zeroY} stroke="currentColor" strokeOpacity="0.2" />
        <path d={path} fill="none" stroke="#84CC16" strokeWidth="2" />
        {cum.map((c, i) => (
          <circle
            key={i}
            cx={pad + i * xstep}
            cy={yscale(c.equity)}
            r={3}
            fill={c.pnl >= 0 ? "#10B981" : "#EF4444"}
          />
        ))}
      </svg>
      <div className="mt-4 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        {cum.slice(-8).reverse().map((c) => (
          <div key={c.day} className="rounded bg-bgSecondary px-2 py-1">
            <div className="text-textMuted">{c.day.slice(0, 10)}</div>
            <div className={c.pnl >= 0 ? "text-bullish" : "text-loss"}>
              <NumberDisplay value={c.pnl} decimals={2} prefix="$" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- shared cells ----------

function SymbolCell({ symbol, exchange }: { symbol: string; exchange: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-xs">{symbol}</span>
      <Badge variant="neutral" className="text-[10px]">{exchange}</Badge>
    </div>
  );
}

function DirectionBadge({ dir }: { dir: string }) {
  if (dir === "long") {
    return (
      <span className="inline-flex items-center gap-1 text-bullish">
        <ArrowUp size={12} /> long
      </span>
    );
  }
  if (dir === "short") {
    return (
      <span className="inline-flex items-center gap-1 text-loss">
        <ArrowDown size={12} /> short
      </span>
    );
  }
  return <span className="text-textMuted">{dir}</span>;
}

function StopCell({ trade }: { trade: BotTrade }) {
  const trailing =
    trade.initial_stop_price !== null && trade.stop_price !== trade.initial_stop_price;
  return (
    <span
      className="inline-flex items-center justify-end gap-1"
      title={
        trailing
          ? `Trailing — initial stop ${trade.initial_stop_price}` +
            (trade.peak_price !== null ? `, peak ${trade.peak_price}` : "")
          : undefined
      }
    >
      {trailing ? (
        <span className="rounded bg-bgSecondary px-1 text-[10px] uppercase tracking-wide text-textMuted">
          trail
        </span>
      ) : null}
      <span className="text-loss">
        <NumberDisplay value={trade.stop_price} decimals={5} />
      </span>
    </span>
  );
}

function PartialChip({ trade }: { trade: BotTrade }) {
  const pnl = trade.partial_pnl_usd;
  return (
    <span
      className="rounded bg-bgSecondary px-1 text-[10px] uppercase tracking-wide text-textMuted"
      title={
        `Partial taken` +
        (trade.partial_exit_price !== null ? ` @ ${trade.partial_exit_price}` : "") +
        (trade.partial_qty !== null ? `, qty ${trade.partial_qty}` : "") +
        (pnl !== null ? `, P&L $${pnl.toFixed(2)}` : "") +
        (trade.partial_exit_at ? ` (${new Date(trade.partial_exit_at).toLocaleString()})` : "")
      }
    >
      ½
    </span>
  );
}

function CloseReasonBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span className="text-textMuted">—</span>;
  const variant =
    reason === "tp"
      ? "bullish"
      : reason === "stop"
        ? "bearish"
        : reason === "kill_switch"
          ? "bearish"
          : "neutral";
  return <Badge variant={variant}>{reason}</Badge>;
}

function RelTime({ iso }: { iso: string | null }) {
  if (!iso) return <span className="text-textMuted">—</span>;
  const d = new Date(iso);
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  const abs = Math.abs(sec);
  let span: string;
  if (abs < 60) span = `${abs}s`;
  else if (abs < 3600) span = `${Math.floor(abs / 60)}m`;
  else if (abs < 86400) span = `${Math.floor(abs / 3600)}h`;
  else span = `${Math.floor(abs / 86400)}d`;
  // Future timestamps (e.g. a pending limit's expire_at) render as "in Xm".
  const txt = sec >= 0 ? `${span} ago` : `in ${span}`;
  return (
    <span className="text-xs text-textSecondary" title={d.toLocaleString()}>
      {txt}
    </span>
  );
}

// ---------- analytics tab ----------

function AnalyticsTab() {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useQuery({
    queryKey: ["bot", "analytics", days],
    queryFn: () => botApi.analytics(days),
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-6 p-4">
      <div className="flex items-center gap-3">
        <span className="text-sm text-textSecondary">Window:</span>
        <Select
          value={String(days)}
          onChange={(e) => setDays(parseInt(e.target.value, 10))}
          className="w-32"
        >
          <option value="7">7 days</option>
          <option value="14">14 days</option>
          <option value="30">30 days</option>
          <option value="90">90 days</option>
        </Select>
      </div>

      {isLoading || !data ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <AnalyticsSection title="By direction" rows={data.by_direction} labelHeader="Side" />
          <AnalyticsSection
            title="By Oracle bucket at entry"
            rows={data.by_oracle_bucket}
            labelHeader="Oracle"
          />
          <AnalyticsSection
            title="By hour of day (UTC)"
            rows={data.by_hour_utc}
            labelHeader="Hour"
            labelFormat={(v) => (v === null ? "—" : `${String(v).padStart(2, "0")}:00`)}
          />
          <AnalyticsSection title="By symbol" rows={data.by_symbol} labelHeader="Symbol" />
        </>
      )}
    </div>
  );
}

function AnalyticsSection({
  title,
  rows,
  labelHeader,
  labelFormat,
}: {
  title: string;
  rows: BotAnalyticsRow[];
  labelHeader: string;
  labelFormat?: (v: string | number | null) => string;
}) {
  const cols: Column<BotAnalyticsRow>[] = [
    {
      key: "label",
      header: labelHeader,
      accessor: (r) => (
        <span className="font-medium">{labelFormat ? labelFormat(r.label) : String(r.label ?? "—")}</span>
      ),
    },
    { key: "n", header: "N", accessor: (r) => <span className="tabular-nums">{r.n_trades}</span> },
    { key: "w", header: "W", accessor: (r) => <span className="tabular-nums">{r.wins}</span> },
    { key: "l", header: "L", accessor: (r) => <span className="tabular-nums">{r.losses}</span> },
    {
      key: "wr",
      header: "Win rate",
      accessor: (r) =>
        r.win_rate == null ? (
          <span className="text-textSecondary">—</span>
        ) : (
          <span className="tabular-nums">{(r.win_rate * 100).toFixed(1)}%</span>
        ),
    },
    {
      key: "r",
      header: "Σ R",
      accessor: (r) => (
        <span
          className={`tabular-nums ${r.realized_r > 0 ? "text-bullish" : r.realized_r < 0 ? "text-bearish" : ""}`}
        >
          {r.realized_r >= 0 ? "+" : ""}
          {r.realized_r.toFixed(2)}
        </span>
      ),
    },
    {
      key: "rnet",
      header: "Σ Rnet",
      accessor: (r) => (
        <span
          className={`tabular-nums ${r.realized_r_net > 0 ? "text-bullish" : r.realized_r_net < 0 ? "text-bearish" : ""}`}
        >
          {r.realized_r_net >= 0 ? "+" : ""}
          {r.realized_r_net.toFixed(2)}
        </span>
      ),
    },
    {
      key: "exp",
      header: "E[R]/trade",
      accessor: (r) =>
        r.expectancy_r == null ? (
          <span className="text-textSecondary">—</span>
        ) : (
          <span
            className={`tabular-nums ${r.expectancy_r > 0 ? "text-bullish" : r.expectancy_r < 0 ? "text-bearish" : ""}`}
          >
            {r.expectancy_r >= 0 ? "+" : ""}
            {r.expectancy_r.toFixed(3)}
          </span>
        ),
    },
    {
      key: "expnet",
      header: "E[Rnet]/trade",
      accessor: (r) =>
        r.expectancy_r_net == null ? (
          <span className="text-textSecondary">—</span>
        ) : (
          <span
            className={`tabular-nums ${r.expectancy_r_net > 0 ? "text-bullish" : r.expectancy_r_net < 0 ? "text-bearish" : ""}`}
          >
            {r.expectancy_r_net >= 0 ? "+" : ""}
            {r.expectancy_r_net.toFixed(3)}
          </span>
        ),
    },
    {
      key: "pnl",
      header: "PnL",
      accessor: (r) => (
        <NumberDisplay
          value={r.realized_pnl_usd}
          decimals={2}
          prefix="$"
          colored
          sign
        />
      ),
    },
  ];

  return (
    <div>
      <div className="mb-2 text-sm font-semibold text-textPrimary">{title}</div>
      <Table
        columns={cols}
        rows={rows}
        rowKey={(r) => String(r.label)}
        dense
        emptyMessage="No data in window."
      />
    </div>
  );
}
