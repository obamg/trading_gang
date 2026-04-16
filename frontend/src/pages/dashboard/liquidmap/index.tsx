import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { MetricCard } from "@/components/ui/MetricCard";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { Skeleton } from "@/components/ui/Skeleton";
import { liquidApi, type HeatmapLevel } from "@/api/modules";

export default function LiquidMapPage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [pending, setPending] = useState(symbol);

  const { data: heatmap, isLoading: hL } = useQuery({
    queryKey: ["liquid", "heatmap", symbol], queryFn: () => liquidApi.heatmap(symbol, 30),
    refetchInterval: 15000,
  });
  const { data: recent } = useQuery({
    queryKey: ["liquid", "recent", symbol], queryFn: () => liquidApi.recent({ symbol, hours: 6 }),
    refetchInterval: 10000,
  });
  const { data: stats } = useQuery({
    queryKey: ["liquid", "stats", symbol], queryFn: () => liquidApi.stats(symbol),
  });

  const maxSize = useMemo(() => {
    const levels = heatmap?.levels ?? [];
    return Math.max(1, ...levels.map((l) => l.size_usd));
  }, [heatmap]);

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">LiquidMap — Liquidation heatmap</h1>
          <p className="text-sm text-textSecondary">Where leverage is concentrated and likely to unwind.</p>
        </div>
        <LiveIndicator />
      </header>

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); setSymbol(pending.toUpperCase()); }}
      >
        <Input label="Symbol" value={pending} onChange={(e) => setPending(e.target.value)} className="w-48" />
        <Button type="submit" variant="secondary">Load</Button>
      </form>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Long liquidations 24h" value={stats?.long_usd_24h ?? null} valuePrefix="$" valueDecimals={0} />
        <MetricCard label="Short liquidations 24h" value={stats?.short_usd_24h ?? null} valuePrefix="$" valueDecimals={0} />
        <MetricCard label="Net bias" value={stats?.net_usd_24h ?? null} valuePrefix="$" valueDecimals={0} />
        <MetricCard label="Buckets" value={heatmap?.levels.length ?? null} valueDecimals={0} />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <h2 className="text-sm font-semibold">{symbol} — liquidation clusters</h2>
          </CardHeader>
          <CardBody>
            {hL ? <Skeleton className="h-64" /> : <HeatmapViz levels={heatmap?.levels ?? []} maxSize={maxSize} />}
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <h2 className="text-sm font-semibold">Large liquidations (&gt;$1M)</h2>
          </CardHeader>
          <CardBody className="flex flex-col gap-2">
            {(recent?.items ?? []).length === 0 ? (
              <p className="text-sm text-textSecondary">No notable liquidations in the last 6h.</p>
            ) : (
              (recent?.items ?? []).slice(0, 25).map((e) => (
                <div key={e.id} className="flex items-center justify-between rounded-md border border-borderSubtle bg-bgElevated px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{e.symbol}</span>
                    <Badge variant={e.side === "long" ? "bearish" : "bullish"}>{e.side.toUpperCase()}</Badge>
                  </div>
                  <div className="flex flex-col items-end text-right">
                    <NumberDisplay value={e.size_usd} decimals={0} prefix="$" className="font-semibold" />
                    <span className="text-xs text-textMuted">@ <NumberDisplay value={e.price} decimals={4} /></span>
                  </div>
                </div>
              ))
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function HeatmapViz({ levels, maxSize }: { levels: HeatmapLevel[]; maxSize: number }) {
  if (levels.length === 0) return <p className="text-sm text-textSecondary">No heatmap data yet for this symbol.</p>;
  const sorted = [...levels].sort((a, b) => b.price - a.price);
  return (
    <div className="flex flex-col gap-1">
      {sorted.map((lv, i) => {
        const pct = (lv.size_usd / maxSize) * 100;
        const isLong = lv.side === "long";
        return (
          <div key={`${lv.side}-${lv.price}-${i}`} className="grid grid-cols-[80px_1fr_80px] items-center gap-2 text-xs">
            <span className="text-right text-textMuted tabular-nums">{isLong ? "LONG" : "SHORT"}</span>
            <div className="relative h-6 bg-bgElevated rounded overflow-hidden">
              <div
                className="absolute inset-y-0 left-0 rounded"
                style={{
                  width: `${pct}%`,
                  backgroundColor: isLong ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)",
                }}
              />
              <span className="absolute inset-0 flex items-center px-2 font-data tabular-nums">
                <NumberDisplay value={lv.price} decimals={4} />
                <span className="ml-2 text-textMuted">· <NumberDisplay value={lv.size_usd} decimals={0} prefix="$" /></span>
              </span>
            </div>
            <span className="text-textMuted tabular-nums">{pct.toFixed(0)}%</span>
          </div>
        );
      })}
    </div>
  );
}
