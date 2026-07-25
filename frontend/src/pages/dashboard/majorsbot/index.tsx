import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Power } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { Select } from "@/components/ui/Select";
import { MetricCard } from "@/components/ui/MetricCard";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { Tabs } from "@/components/ui/Tabs";
import {
  majorsbotApi,
  type MajorsBotAnalyticsRow,
  type MajorsBotStrategy,
  type MajorsBotTrade,
} from "@/api/modules";

// Forward-test verdict gates: minimum closed trades before a strategy can be judged.
const VERDICT_N: Record<MajorsBotStrategy, number> = {
  volevent: 30,
  fundingfade: 100,
};

const STRATEGY_META: Record<
  MajorsBotStrategy,
  { label: string; color: string; blurb: string }
> = {
  volevent: {
    label: "volevent",
    color: "#0EA5E9",
    blurb: "1h vol-event momentum retrace — limit entries",
  },
  fundingfade: {
    label: "fundingfade",
    color: "#D946EF",
    blurb: "funding-extreme fade — market entries",
  },
};

type StatusKey = "all" | "pending" | "open" | "closed" | "cancelled";

export default function MajorsBotPage() {
  const [days, setDays] = useState(90);
  const [tab, setTab] = useState<"trades" | "symbols">("trades");

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["majorsbot", "status"],
    queryFn: majorsbotApi.status,
    refetchInterval: 15_000,
  });
  const { data: analytics, isLoading: analyticsLoading } = useQuery({
    queryKey: ["majorsbot", "analytics", days],
    queryFn: () => majorsbotApi.analytics(days),
    refetchInterval: 60_000,
  });

  const universe = status?.config.symbols ?? [];

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">MajorsBot</h1>
          <p className="text-sm text-textSecondary">
            Paper trading two strategies on a fixed {universe.length || 10}-major universe:{" "}
            <span className="text-textPrimary">volevent</span> +{" "}
            <span className="text-textPrimary">fundingfade</span>.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {status?.enabled ? <LiveIndicator /> : <PausedChip />}
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
            label="Open Positions"
            value={status.open_positions}
            valueDecimals={0}
            valueSuffix={` / ${status.max_concurrent}`}
          />
          <MetricCard label="Pending Orders" value={status.pending_orders} valueDecimals={0} />
          <MetricCard
            label="Universe"
            value={universe.length}
            valueDecimals={0}
            valueSuffix=" majors"
          />
        </div>
      )}

      {/* Per-strategy split — the centerpiece: judge the two strategies independently. */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-textPrimary">
          Strategy forward test <span className="text-textMuted font-normal">({days}d window)</span>
        </h2>
        <Select value={String(days)} onChange={(e) => setDays(parseInt(e.target.value, 10))}>
          <option value="30">30 days</option>
          <option value="90">90 days</option>
          <option value="180">180 days</option>
          <option value="365">1 year</option>
        </Select>
      </div>
      {analyticsLoading || !analytics || !status ? (
        <Skeleton className="h-56" />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {(["volevent", "fundingfade"] as MajorsBotStrategy[]).map((s) => (
            <StrategyPanel
              key={s}
              strategy={s}
              enabled={
                s === "volevent"
                  ? status.config.volevent_enabled
                  : status.config.fundingfade_enabled
              }
              row={analytics.by_strategy.find((r) => r.label === s)}
              dirRows={analytics.by_strategy_direction.filter((r) =>
                r.label.startsWith(`${s}/`),
              )}
            />
          ))}
        </div>
      )}

      <Card>
        <CardHeader>
          <Tabs
            tabs={[
              { key: "trades", label: "Trades" },
              { key: "symbols", label: "By symbol" },
            ]}
            active={tab}
            onChange={(k) => setTab(k as "trades" | "symbols")}
          />
        </CardHeader>
        <CardBody className="p-0">
          {tab === "trades" ? <TradesTab /> : <BySymbolTab rows={analytics?.by_symbol ?? []} />}
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

// ---------- strategy panel ----------

function StrategyPanel({
  strategy,
  enabled,
  row,
  dirRows,
}: {
  strategy: MajorsBotStrategy;
  enabled: boolean;
  row: MajorsBotAnalyticsRow | undefined;
  dirRows: MajorsBotAnalyticsRow[];
}) {
  const meta = STRATEGY_META[strategy];
  const n = row?.n_trades ?? 0;
  const gate = VERDICT_N[strategy];
  const progress = Math.min(1, n / gate);
  const avgRNet = row?.avg_r_net ?? null;

  return (
    <div className="rounded-lg border border-borderSubtle bg-bgCard p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant="module" accentColor={meta.color}>
            {meta.label}
          </Badge>
          {enabled ? (
            <span className="text-xs text-bullish">enabled</span>
          ) : (
            <span className="text-xs text-textMuted">disabled</span>
          )}
        </div>
        <span className="text-xs text-textMuted">{meta.blurb}</span>
      </div>

      <div className="mt-4 flex items-baseline gap-2">
        <span
          className={`text-2xl font-bold tabular-nums ${
            avgRNet === null ? "text-textMuted" : avgRNet >= 0 ? "text-bullish" : "text-loss"
          }`}
        >
          {avgRNet === null ? "—" : `${avgRNet >= 0 ? "+" : ""}${avgRNet.toFixed(3)}R`}
        </span>
        <span className="text-xs text-textMuted">avg net R / trade</span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm md:grid-cols-3">
        <StatCell label="Closed" value={<span className="tabular-nums">{n}</span>} />
        <StatCell
          label="Win rate"
          value={
            row?.win_rate == null ? (
              <span className="text-textMuted">—</span>
            ) : (
              <span className="tabular-nums">{(row.win_rate * 100).toFixed(1)}%</span>
            )
          }
        />
        <StatCell
          label="Σ Net R"
          value={
            <span
              className={`tabular-nums ${
                (row?.realized_r_net ?? 0) > 0
                  ? "text-bullish"
                  : (row?.realized_r_net ?? 0) < 0
                    ? "text-loss"
                    : ""
              }`}
            >
              {(row?.realized_r_net ?? 0) >= 0 ? "+" : ""}
              {(row?.realized_r_net ?? 0).toFixed(2)}
            </span>
          }
        />
        <StatCell
          label="P&L"
          value={<NumberDisplay value={row?.realized_pnl_usd ?? 0} decimals={2} prefix="$" colored sign />}
        />
        <StatCell
          label="Fees"
          value={<NumberDisplay value={row?.fees_usd ?? 0} decimals={2} prefix="$" />}
        />
        <StatCell
          label="Funding"
          value={<NumberDisplay value={row?.funding_pnl_usd ?? 0} decimals={2} prefix="$" colored sign />}
        />
      </div>

      {/* Verdict-gate progress: n closed vs the minimum sample for a call. */}
      <div className="mt-4">
        <div className="flex items-center justify-between text-xs text-textMuted">
          <span>Verdict gate</span>
          <span className="tabular-nums">
            {n} / {gate} closed
          </span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-bgSecondary">
          <div
            className="h-full rounded"
            style={{ width: `${progress * 100}%`, backgroundColor: meta.color }}
          />
        </div>
      </div>

      {dirRows.length > 0 ? (
        <div className="mt-3 space-y-1 border-t border-borderSubtle pt-2 text-xs">
          {dirRows.map((d) => (
            <div key={d.label} className="flex items-center justify-between">
              <span className="text-textMuted">{d.label.split("/")[1]}</span>
              <span className="tabular-nums text-textSecondary">
                n {d.n_trades} · {d.win_rate == null ? "—" : `${(d.win_rate * 100).toFixed(0)}%`} ·{" "}
                <span className={d.realized_r_net >= 0 ? "text-bullish" : "text-loss"}>
                  {d.realized_r_net >= 0 ? "+" : ""}
                  {d.realized_r_net.toFixed(2)}R net
                </span>
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StatCell({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-textMuted">{label}</div>
      <div>{value}</div>
    </div>
  );
}

// ---------- trades tab ----------

function TradesTab() {
  const [status, setStatus] = useState<StatusKey>("all");
  const [strategy, setStrategy] = useState("");
  const [symbol, setSymbol] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["majorsbot", "trades", { status, strategy, symbol }],
    queryFn: () =>
      majorsbotApi.trades({
        status,
        strategy: strategy || undefined,
        symbol: symbol || undefined,
        limit: 200,
      }),
    refetchInterval: 30_000,
  });
  const rows = data?.items ?? [];

  const cols: Column<MajorsBotTrade>[] = [
    {
      key: "strategy",
      header: "Strategy",
      accessor: (r) => (
        <Badge variant="module" accentColor={STRATEGY_META[r.strategy]?.color ?? "#64748B"}>
          {r.strategy}
        </Badge>
      ),
    },
    { key: "symbol", header: "Symbol", accessor: (r) => <SymbolCell symbol={r.symbol} exchange={r.exchange} /> },
    { key: "dir", header: "Side", accessor: (r) => <DirectionBadge dir={r.direction} /> },
    { key: "status", header: "Status", accessor: (r) => <StatusBadge status={r.status} /> },
    {
      key: "entry",
      header: "Entry / limit",
      align: "right",
      accessor: (r) => (
        <span title={r.entry_mode ? `${r.entry_mode} entry` : undefined}>
          <NumberDisplay
            value={r.status === "pending" ? (r.limit_price ?? r.entry_price) : r.entry_price}
            decimals={5}
          />
        </span>
      ),
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
      key: "fees",
      header: "Fees",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.fees_usd} decimals={2} prefix="$" />,
    },
    {
      key: "funding",
      header: "Funding",
      align: "right",
      accessor: (r) => <NumberDisplay value={r.funding_pnl_usd} decimals={2} prefix="$" colored sign />,
    },
    { key: "opened", header: "Opened", accessor: (r) => <RelTime iso={r.entry_at} /> },
    { key: "closed", header: "Closed", accessor: (r) => <RelTime iso={r.closed_at} /> },
  ];

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-borderSubtle px-4 py-3">
        <Tabs
          className="border-b-0"
          tabs={[
            { key: "all", label: "All" },
            { key: "pending", label: "Pending" },
            { key: "open", label: "Open" },
            { key: "closed", label: "Closed" },
            { key: "cancelled", label: "Cancelled" },
          ]}
          active={status}
          onChange={(k) => setStatus(k as StatusKey)}
        />
        <div className="ml-auto flex items-center gap-2 text-sm">
          <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="">both strategies</option>
            <option value="volevent">volevent</option>
            <option value="fundingfade">fundingfade</option>
          </Select>
          <input
            placeholder="symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="w-32 rounded border border-borderSubtle bg-bgSecondary px-2 py-1 text-xs"
          />
        </div>
      </div>
      {isLoading ? (
        <Skeleton className="m-4 h-48" />
      ) : (
        <Table columns={cols} rows={rows} rowKey={(r) => r.id} emptyMessage="No trades yet." />
      )}
    </div>
  );
}

// ---------- by-symbol analytics tab ----------

function BySymbolTab({ rows }: { rows: MajorsBotAnalyticsRow[] }) {
  const cols: Column<MajorsBotAnalyticsRow>[] = [
    {
      key: "symbol",
      header: "Symbol",
      accessor: (r) => <span className="font-mono text-xs">{r.label}</span>,
    },
    { key: "n", header: "N", accessor: (r) => <span className="tabular-nums">{r.n_trades}</span> },
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
        <NumberDisplay value={r.realized_pnl_usd} decimals={2} prefix="$" colored sign />
      ),
    },
    {
      key: "fees",
      header: "Fees",
      accessor: (r) => <NumberDisplay value={r.fees_usd} decimals={2} prefix="$" />,
    },
  ];
  return (
    <Table
      columns={cols}
      rows={rows}
      rowKey={(r) => r.label}
      dense
      emptyMessage="No closed trades in window."
    />
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

function StatusBadge({ status }: { status: MajorsBotTrade["status"] }) {
  const variant =
    status === "pending"
      ? "warning"
      : status === "open"
        ? "bullish"
        : status === "cancelled"
          ? "neutral"
          : "neutral";
  return <Badge variant={variant}>{status}</Badge>;
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
  const txt = sec >= 0 ? `${span} ago` : `in ${span}`;
  return (
    <span className="text-xs text-textSecondary" title={d.toLocaleString()}>
      {txt}
    </span>
  );
}
