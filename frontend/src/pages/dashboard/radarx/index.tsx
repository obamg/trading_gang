import { useMemo, useState } from "react";
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
import { radarxApi, type RadarXAlert, type TopMover } from "@/api/modules";
import { useModuleAlerts } from "@/hooks/useModuleAlerts";
import { MODULE_BY_KEY } from "@/components/layout/modules";

type FeedFilter = "all" | "divergence";

function divergenceScore(a: RadarXAlert): number {
  const absPct = Math.abs(a.price_change_pct ?? 0);
  if (absPct === 0) return a.z_score * 100;
  return a.z_score / absPct;
}

function isDivergence(a: RadarXAlert): boolean {
  const absPct = Math.abs(a.price_change_pct ?? 0);
  return a.z_score >= 3 && absPct < 1;
}

export default function RadarXPage() {
  const nav = useNavigate();
  const [filter, setFilter] = useState<FeedFilter>("all");
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
      triggered_at: (a.data?.triggered_at as string) ?? new Date(a.receivedAt ?? Date.now()).toISOString(),
    }));
    const map = new Map<string, RadarXAlert>();
    [...live, ...(alertsData?.items ?? [])].forEach((a) => map.set(a.id, a));
    return Array.from(map.values()).sort(
      (a, b) => new Date(b.triggered_at).getTime() - new Date(a.triggered_at).getTime(),
    );
  }, [liveAlerts, alertsData]);

  const filtered = useMemo(() => {
    if (filter === "all") return combined;
    return combined.filter(isDivergence).sort((a, b) => divergenceScore(b) - divergenceScore(a));
  }, [combined, filter]);

  const divergenceCount = useMemo(() => combined.filter(isDivergence).length, [combined]);

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
        <LiveIndicator />
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
              onChange={(k) => setFilter(k as FeedFilter)}
            />
          </CardHeader>
          {filter === "divergence" && (
            <div className="border-b border-borderSubtle bg-bgElevated px-4 py-2 text-xs text-textSecondary">
              Volume spike (Z &ge; 3) with price move &lt; 1% — accumulation before a move.
            </div>
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
                      {filter === "divergence" && <span className="ml-1.5 text-warning">· Div {divergenceScore(a).toFixed(1)}</span>}
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
