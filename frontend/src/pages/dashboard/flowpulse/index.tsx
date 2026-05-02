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

function DirectionBadge({ direction, intensity }: { direction: string | null; intensity: number | null }) {
  const variant = direction === "bullish" ? "bullish" : direction === "bearish" ? "bearish" : "neutral";
  const pct = intensity != null ? `${(intensity * 100).toFixed(0)}%` : "";
  return (
    <Badge variant={variant}>
      {direction ?? "neutral"} {pct}
    </Badge>
  );
}

function ImbalanceBar({ ratio }: { ratio: number | null }) {
  if (ratio == null) return <span className="text-textMuted">-</span>;
  const bidPct = Math.min(100, (ratio / (ratio + 1)) * 100);
  const askPct = 100 - bidPct;
  const extreme = ratio >= 3 || ratio <= 0.33;
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-4 w-24 overflow-hidden rounded bg-bgElevated sm:w-32">
        <div className="absolute inset-y-0 left-0 bg-profit/60" style={{ width: `${bidPct}%` }} />
        <div className="absolute inset-y-0 right-0 bg-loss/60" style={{ width: `${askPct}%` }} />
      </div>
      <span className={`text-xs tabular-nums ${extreme ? "text-warning font-semibold" : "text-textSecondary"}`}>
        {ratio.toFixed(2)}
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
  const { data: extremes, isLoading: eL } = useQuery({
    queryKey: ["flowpulse", "extremes"],
    queryFn: () => flowApi.extremes(1),
    refetchInterval: 60_000,
  });

  const lastUpdated = useMemo(() => {
    if (overview?.snapshot_at) return new Date(overview.snapshot_at);
    return null;
  }, [overview]);

  const rows = overview?.items ?? [];

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">FlowPulse — Order Flow Signals</h1>
          <p className="text-sm text-textSecondary">
            Book imbalance, taker volume ratio, and top trader positioning.
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
          <h2 className="text-sm font-semibold">Current Order Flow — All Symbols</h2>
        </CardHeader>
        <CardBody className="p-0">
          {oL ? <Skeleton className="m-4 h-64" /> : <FlowTable rows={rows} />}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold">Extreme Signals (last 1h)</h2>
        </CardHeader>
        <CardBody className="p-0">
          {eL ? (
            <Skeleton className="m-4 h-40" />
          ) : (
            <FlowTable rows={extremes?.items ?? []} />
          )}
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
      key: "direction",
      header: "Direction",
      accessor: (r) => <DirectionBadge direction={r.direction} intensity={r.intensity} />,
      sortValue: (r) => (r.intensity ?? 0) * (r.direction === "bullish" ? 1 : r.direction === "bearish" ? -1 : 0),
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
        return (
          <span className={v > 1.5 ? "text-profit" : v < 0.67 ? "text-loss" : "text-textPrimary"}>
            {v.toFixed(3)}
          </span>
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
        const extreme = long >= 70 || short >= 70;
        return (
          <span className={`text-xs tabular-nums ${extreme ? "text-warning font-semibold" : "text-textSecondary"}`}>
            {long.toFixed(0)}% / {short.toFixed(0)}%
          </span>
        );
      },
      sortValue: (r) => r.top_long_ratio ?? 50,
    },
    {
      key: "bid_usd",
      header: "Bid Depth",
      align: "right",
      accessor: (r) => <span className="text-textSecondary">{formatUsd(r.bid_usd)}</span>,
      sortValue: (r) => r.bid_usd ?? 0,
    },
    {
      key: "ask_usd",
      header: "Ask Depth",
      align: "right",
      accessor: (r) => <span className="text-textSecondary">{formatUsd(r.ask_usd)}</span>,
      sortValue: (r) => r.ask_usd ?? 0,
    },
  ];

  return (
    <Table
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      emptyMessage="No flow signals yet. Data populates every 2 minutes."
    />
  );
}
