import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricCard } from "@/components/ui/MetricCard";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { LastUpdated } from "@/components/ui/LastUpdated";
import { flowApi, type FlowSignalRow } from "@/api/modules";

function formatUsd(v: number | null): string {
  if (v == null) return "-";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

type SignalDir = "bullish" | "bearish" | "neutral";

function getBookDir(imb: number | null): SignalDir {
  if (imb == null) return "neutral";
  if (imb >= 1.3) return "bullish";
  if (imb <= 0.77) return "bearish";
  return "neutral";
}

function getTakerDir(ratio: number | null): SignalDir {
  if (ratio == null) return "neutral";
  if (ratio >= 1.15) return "bullish";
  if (ratio <= 0.87) return "bearish";
  return "neutral";
}

function getTopDir(longRatio: number | null): SignalDir {
  if (longRatio == null) return "neutral";
  if (longRatio >= 55) return "bullish";
  if (longRatio <= 45) return "bearish";
  return "neutral";
}

function getConfluence(r: FlowSignalRow): { count: number; direction: SignalDir } {
  const book = getBookDir(r.book_imbalance);
  const taker = getTakerDir(r.taker_ratio);
  const top = getTopDir(r.top_long_ratio);

  const signals = [book, taker, top];
  const bullish = signals.filter((s) => s === "bullish").length;
  const bearish = signals.filter((s) => s === "bearish").length;

  if (bullish >= 2) return { count: bullish, direction: "bullish" };
  if (bearish >= 2) return { count: bearish, direction: "bearish" };
  return { count: 0, direction: "neutral" };
}

function getTotalDepth(r: FlowSignalRow): number {
  return (r.bid_usd ?? 0) + (r.ask_usd ?? 0);
}

function ConfluenceBadge({ row }: { row: FlowSignalRow }) {
  const { count, direction } = getConfluence(row);
  if (count === 0) {
    return <span className="text-xs text-textMuted">mixed</span>;
  }
  const variant = direction === "bullish" ? "bullish" : "bearish";
  return (
    <Badge variant={variant}>
      {count}/3 {direction}
    </Badge>
  );
}

function SignalDot({ dir }: { dir: SignalDir }) {
  if (dir === "bullish") return <span className="inline-block h-2 w-2 rounded-full bg-profit" />;
  if (dir === "bearish") return <span className="inline-block h-2 w-2 rounded-full bg-loss" />;
  return <span className="inline-block h-2 w-2 rounded-full bg-textMuted/40" />;
}

function ImbalanceBar({ ratio }: { ratio: number | null }) {
  if (ratio == null) return <span className="text-textMuted">-</span>;
  const capped = Math.min(ratio, 10);
  const bidPct = Math.min(100, (capped / (capped + 1)) * 100);
  const askPct = 100 - bidPct;
  const dir = getBookDir(ratio);
  return (
    <div className="flex items-center gap-2">
      <SignalDot dir={dir} />
      <div className="relative h-4 w-20 overflow-hidden rounded bg-bgElevated sm:w-28">
        <div className="absolute inset-y-0 left-0 bg-profit/60" style={{ width: `${bidPct}%` }} />
        <div className="absolute inset-y-0 right-0 bg-loss/60" style={{ width: `${askPct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-textSecondary">
        {ratio >= 10 ? ratio.toFixed(0) : ratio.toFixed(2)}
      </span>
    </div>
  );
}

export default function FlowPulsePage() {
  const { data: overview, isLoading: oL } = useQuery({
    queryKey: ["flowpulse", "overview"],
    queryFn: flowApi.overview,
    refetchInterval: 60_000,
  });
  const { data: stats } = useQuery({
    queryKey: ["flowpulse", "stats"],
    queryFn: flowApi.stats,
    refetchInterval: 120_000,
  });

  const lastUpdated = useMemo(() => {
    if (overview?.snapshot_at) return new Date(overview.snapshot_at);
    return null;
  }, [overview]);

  const sorted = useMemo(() => {
    const items = overview?.items ?? [];
    return [...items].sort((a, b) => {
      const ca = getConfluence(a);
      const cb = getConfluence(b);
      // 1. Confluence count (3/3 > 2/3 > mixed)
      if (cb.count !== ca.count) return cb.count - ca.count;
      // 2. Total depth as liquidity filter (higher = more reliable)
      const depthDiff = getTotalDepth(b) - getTotalDepth(a);
      if (Math.abs(depthDiff) > 5_000) return depthDiff;
      // 3. Intensity as tiebreaker
      return (b.intensity ?? 0) - (a.intensity ?? 0);
    });
  }, [overview]);

  const aligned = useMemo(() => sorted.filter((r) => getConfluence(r).count >= 2), [sorted]);
  const mixed = useMemo(() => sorted.filter((r) => getConfluence(r).count < 2), [sorted]);

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">FlowPulse — Order Flow Signals</h1>
          <p className="text-sm text-textSecondary">
            Book imbalance, taker volume ratio, and top trader positioning. Best signals have 3/3 confluence.
          </p>
        </div>
        <LastUpdated date={lastUpdated} label="Last scan" />
      </header>

      <section className="grid grid-cols-2 gap-2 sm:grid-cols-4 md:gap-3">
        <MetricCard label="Snapshots (24h)" value={stats?.snapshots_24h ?? null} valueDecimals={0} />
        <MetricCard label="Bullish signals" value={stats?.bullish_24h ?? null} valueDecimals={0} />
        <MetricCard label="Bearish signals" value={stats?.bearish_24h ?? null} valueDecimals={0} />
        <MetricCard
          label="Avg Taker Ratio"
          value={stats?.avg_taker_ratio ?? null}
          valueDecimals={3}
        />
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Aligned Signals</h2>
            <span className="text-xs text-textMuted">2/3 or 3/3 indicators agree</span>
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {oL ? <Skeleton className="m-4 h-64" /> : <FlowTable rows={aligned} />}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Mixed / Conflicting</h2>
            <span className="text-xs text-textMuted">no clear consensus</span>
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {oL ? <Skeleton className="m-4 h-40" /> : <FlowTable rows={mixed} />}
        </CardBody>
      </Card>
    </div>
  );
}

function FlowTable({ rows }: { rows: FlowSignalRow[] }) {
  const columns: Column<FlowSignalRow>[] = [
    {
      key: "symbol",
      header: "Symbol",
      accessor: (r) => <span className="font-semibold">{r.symbol}</span>,
    },
    {
      key: "confluence",
      header: "Confluence",
      accessor: (r) => <ConfluenceBadge row={r} />,
      sortValue: (r) => {
        const c = getConfluence(r);
        return c.count * 10 + (r.intensity ?? 0);
      },
    },
    {
      key: "book",
      header: "Book Imbalance",
      accessor: (r) => <ImbalanceBar ratio={r.book_imbalance} />,
      sortValue: (r) => r.book_imbalance ?? 1,
    },
    {
      key: "taker",
      header: "Taker Ratio",
      align: "right",
      sortValue: (r) => r.taker_ratio ?? 0,
      accessor: (r) => {
        if (r.taker_ratio == null) return <span className="text-textMuted">-</span>;
        const v = r.taker_ratio;
        const dir = getTakerDir(v);
        return (
          <div className="flex items-center justify-end gap-1.5">
            <SignalDot dir={dir} />
            <span className={v > 1.5 ? "text-profit" : v < 0.67 ? "text-loss" : "text-textPrimary"}>
              {v.toFixed(3)}
            </span>
          </div>
        );
      },
    },
    {
      key: "top_long",
      header: "Top Traders L/S",
      accessor: (r) => {
        if (r.top_long_ratio == null) return <span className="text-textMuted">-</span>;
        const long = r.top_long_ratio;
        const short = r.top_short_ratio ?? (100 - long);
        const dir = getTopDir(long);
        return (
          <div className="flex items-center gap-1.5">
            <SignalDot dir={dir} />
            <span className={`text-xs tabular-nums ${long >= 65 || short >= 65 ? "text-warning font-semibold" : "text-textSecondary"}`}>
              {long.toFixed(0)}% / {short.toFixed(0)}%
            </span>
          </div>
        );
      },
      sortValue: (r) => r.top_long_ratio ?? 50,
    },
    {
      key: "depth",
      header: "Total Depth",
      align: "right",
      accessor: (r) => {
        const total = getTotalDepth(r);
        return (
          <span className={`text-textSecondary ${total < 5_000 ? "text-textMuted" : ""}`}>
            {formatUsd(total)}
          </span>
        );
      },
      sortValue: (r) => getTotalDepth(r),
    },
  ];

  return (
    <Table
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      emptyMessage="No signals in this category."
    />
  );
}
