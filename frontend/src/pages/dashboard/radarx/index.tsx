import { useMemo, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricCard } from "@/components/ui/MetricCard";
import { AlertItem } from "@/components/ui/AlertItem";
import { Tabs } from "@/components/ui/Tabs";
import { Table, type Column } from "@/components/ui/Table";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { PercentChange } from "@/components/ui/PercentChange";
import { Skeleton } from "@/components/ui/Skeleton";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import { radarxApi, type RadarXAlert, type TopMover } from "@/api/modules";
import { useModuleAlerts } from "@/hooks/useModuleAlerts";
import { MODULE_BY_KEY } from "@/components/layout/modules";

type FeedFilter = "all" | "divergence";

type SymbolCount = { symbol: string; count: number; avgScore: number };

function SymbolOccurrences({
  items,
  onSelect,
  selected,
}: {
  items: SymbolCount[];
  onSelect: (s: string | null) => void;
  selected: string | null;
}) {
  if (items.length === 0) return null;
  const max = items[0].count;
  return (
    <div className="border-b border-borderSubtle bg-bgElevated px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-textSecondary">
          Asset Frequency ({items.length} symbols)
        </span>
        {selected && (
          <button
            onClick={() => onSelect(null)}
            className="text-xs text-primary-400 hover:underline"
          >
            Clear filter
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((s) => (
          <button
            key={s.symbol}
            onClick={() => onSelect(selected === s.symbol ? null : s.symbol)}
            className={`group flex items-center gap-1.5 rounded px-2 py-1 text-xs transition-colors ${
              selected === s.symbol
                ? "bg-primary-subtle ring-1 ring-primary-400"
                : "bg-bgBase hover:bg-bgHover"
            }`}
          >
            <span className="font-semibold text-textPrimary">{s.symbol.replace("USDT", "")}</span>
            <span className="tabular-nums text-textMuted">{s.count}x</span>
            <div className="relative h-1.5 w-8 overflow-hidden rounded-full bg-borderSubtle">
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-warning"
                style={{ width: `${(s.count / max) * 100}%` }}
              />
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function getDivergenceScore(a: RadarXAlert): number {
  if (a.divergence_score != null) return a.divergence_score;
  const absPct = Math.abs(a.price_change_pct ?? 0);
  if (absPct === 0) return a.z_score * 100;
  return a.z_score / absPct;
}

function isDivergence(a: RadarXAlert): boolean {
  if (a.is_divergence != null) return a.is_divergence;
  const absPct = Math.abs(a.price_change_pct ?? 0);
  return a.z_score >= 3 && absPct < 1;
}

export default function RadarXPage() {
  const nav = useNavigate();
  const [filter, setFilter] = useState<FeedFilter>("all");
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const { data: stats } = useQuery({ queryKey: ["radarx", "stats"], queryFn: radarxApi.stats });
  const { data: alertsData, isLoading: loadingAlerts } = useQuery({
    queryKey: ["radarx", "alerts"],
    queryFn: () => radarxApi.alerts({ hours: 24, limit: 200 }),
    refetchInterval: 30000,
  });
  const { data: moversData } = useQuery({
    queryKey: ["radarx", "top-movers"],
    queryFn: () => radarxApi.topMovers(20),
    refetchInterval: 5000,
  });
  const liveAlerts = useModuleAlerts("radarx");

  const accent = MODULE_BY_KEY.radarx.color;

  const combined = useMemo<RadarXAlert[]>(() => {
    const live = liveAlerts.map((a) => ({
      id: (a.data?.id as string) ?? String(a.receivedAt),
      symbol: a.data?.symbol as string,
      z_score: (a.data?.z_score as number) ?? 0,
      ratio: (a.data?.ratio as number) ?? 0,
      candle_volume_usd: (a.data?.candle_volume_usd as number) ?? 0,
      avg_volume_usd: (a.data?.avg_volume_usd as number) ?? 0,
      price: (a.data?.price as number) ?? 0,
      price_change_pct: (a.data?.price_change_pct as number) ?? null,
      volume_24h_usd: (a.data?.volume_24h_usd as number) ?? null,
      is_divergence: (a.data?.is_divergence as boolean) ?? undefined,
      divergence_score: (a.data?.divergence_score as number) ?? undefined,
      triggered_at: (a.data?.triggered_at as string) ?? new Date(a.receivedAt ?? Date.now()).toISOString(),
    }));
    const map = new Map<string, RadarXAlert>();
    [...live, ...(alertsData?.items ?? [])].forEach((a) => map.set(a.id, a));
    return Array.from(map.values()).sort(
      (a, b) => new Date(b.triggered_at).getTime() - new Date(a.triggered_at).getTime(),
    );
  }, [liveAlerts, alertsData]);

  const divergences = useMemo(
    () =>
      combined.filter(isDivergence).sort((a, b) => {
        const scoreDiff = getDivergenceScore(b) - getDivergenceScore(a);
        if (scoreDiff !== 0) return scoreDiff;
        return b.candle_volume_usd - a.candle_volume_usd;
      }),
    [combined],
  );

  const symbolCounts = useMemo<SymbolCount[]>(() => {
    const map = new Map<string, { count: number; totalScore: number }>();
    for (const a of divergences) {
      const prev = map.get(a.symbol) ?? { count: 0, totalScore: 0 };
      map.set(a.symbol, { count: prev.count + 1, totalScore: prev.totalScore + getDivergenceScore(a) });
    }
    return Array.from(map.entries())
      .map(([symbol, { count, totalScore }]) => ({ symbol, count, avgScore: totalScore / count }))
      .sort((a, b) => b.count - a.count || b.avgScore - a.avgScore);
  }, [divergences]);

  const handleSymbolSelect = useCallback((s: string | null) => {
    setSelectedSymbol(s);
    if (s && filter !== "divergence") setFilter("divergence");
  }, [filter]);

  const filtered = useMemo(() => {
    const base = filter === "all" ? combined : divergences;
    if (selectedSymbol) return base.filter((a) => a.symbol === selectedSymbol);
    return base;
  }, [combined, divergences, filter, selectedSymbol]);

  const divergenceCount = divergences.length;

  const lastUpdated = useMemo(() => {
    if (combined.length === 0) return null;
    return new Date(combined[0].triggered_at);
  }, [combined]);

  type Row = TopMover & { _rank: number };
  const columns: Column<Row>[] = [
    { key: "rank", header: "#", accessor: (r) => <span className="text-textMuted">{r._rank}</span>, align: "left" },
    { key: "symbol", header: "Symbol", accessor: (r) => <span className="font-semibold">{r.symbol}</span>, sortValue: (r) => r.symbol },
    { key: "price", header: "Price", accessor: (r) => <NumberDisplay value={r.price} decimals={4} />, align: "right", sortValue: (r) => r.price },
    { key: "z", header: "Z-Score", accessor: (r) => <NumberDisplay value={r.z_score} decimals={2} />, align: "right", sortValue: (r) => r.z_score },
    { key: "ratio", header: "Ratio", accessor: (r) => <NumberDisplay value={r.ratio} decimals={2} suffix="x" />, align: "right", sortValue: (r) => r.ratio },
  ];
  const rankedRows: Row[] = (moversData?.items ?? []).map((m, i) => ({ ...m, _rank: i + 1 }));

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">RadarX — Volume spike detection</h1>
          <p className="text-sm text-textSecondary">Live z-score alerts on 5-minute candles.</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <LiveIndicator />
          <LastUpdated date={lastUpdated} />
        </div>
      </header>

      <section className="grid grid-cols-2 gap-2 md:grid-cols-4 md:gap-3">
        <MetricCard label="Alerts 24h" value={stats?.alerts_24h ?? null} valueDecimals={0} />
        <MetricCard label="Avg Z-Score" value={stats?.avg_z_score ?? null} />
        <MetricCard label="Top Symbol" value={null} valueSuffix={stats?.top_symbol ?? "—"} valueDecimals={0} />
        <MetricCard label="Divergences" value={divergenceCount} valueDecimals={0} />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <Tabs
              tabs={[
                { key: "all", label: `All (${combined.length})` },
                { key: "divergence", label: `Divergence (${divergenceCount})` },
              ]}
              active={filter}
              onChange={(k) => { setFilter(k as FeedFilter); setSelectedSymbol(null); }}
            />
          </CardHeader>
          {filter === "divergence" && (
            <>
              <div className="border-b border-borderSubtle bg-bgElevated px-4 py-2 text-xs text-textSecondary">
                Volume spike (Z &ge; 3) with price move &lt; 1% — accumulation before a move.
              </div>
              <SymbolOccurrences
                items={symbolCounts}
                onSelect={handleSymbolSelect}
                selected={selectedSymbol}
              />
            </>
          )}
          <CardBody className="flex flex-col gap-2">
            {loadingAlerts ? (
              <div className="flex flex-col gap-2"><Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
            ) : filtered.length === 0 ? (
              <p className="text-sm text-textSecondary">
                {filter === "divergence" ? "No divergences detected in the last 24h." : "No alerts in the last 24 hours."}
              </p>
            ) : (
              filtered.slice(0, 50).map((a) => (
                <AlertItem
                  key={a.id}
                  symbol={a.symbol}
                  moduleLabel="RadarX"
                  accentColor={accent}
                  timestamp={new Date(a.triggered_at).toLocaleTimeString()}
                  stats={
                    <span>
                      Z <NumberDisplay value={a.z_score} decimals={2} />
                      {" · "}Ratio <NumberDisplay value={a.ratio} decimals={2} suffix="x" />
                      {" · "}Price <NumberDisplay value={a.price} decimals={4} />
                      {a.price_change_pct != null ? <> {" · "}<PercentChange value={a.price_change_pct} /></> : null}
                      {isDivergence(a) && <span className="ml-1.5 text-warning">· Div {getDivergenceScore(a).toFixed(1)}</span>}
                    </span>
                  }
                  chartUrl={`https://www.tradingview.com/chart/?symbol=BINANCE:${a.symbol}.P`}
                  actionLabel="Open in RiskCalc"
                  onAction={() => nav(`/riskcalc?symbol=${a.symbol}&entry=${a.price}`)}
                />
              ))
            )}
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <h2 className="text-sm font-semibold">Top movers (live)</h2>
            <span className="text-xs text-textSecondary">5s refresh</span>
          </CardHeader>
          <CardBody className="p-0">
            <Table columns={columns} rows={rankedRows} rowKey={(r) => r.symbol} dense />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
