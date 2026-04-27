import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricCard } from "@/components/ui/MetricCard";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { LastUpdated } from "@/components/ui/LastUpdated";
import { macroApi } from "@/api/modules";

export default function MacroPage() {
  const { data: snap, isLoading: sL } = useQuery({ queryKey: ["macro", "snapshot"], queryFn: macroApi.snapshot });
  const { data: score } = useQuery({ queryKey: ["macro", "score"], queryFn: () => macroApi.score() });
  const { data: cal, isLoading: cL } = useQuery({ queryKey: ["macro", "calendar"], queryFn: () => macroApi.calendar(72) });

  const macroScore = score?.macro_score ?? snap?.macro_score ?? null;
  const scoreColor =
    macroScore == null ? "text-textSecondary" :
    macroScore > 20 ? "text-profit" :
    macroScore < -20 ? "text-loss" : "text-warning";

  const lastUpdated = useMemo(() => {
    if (snap?.snapshot_at) return new Date(snap.snapshot_at);
    return null;
  }, [snap]);

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">MacroPulse — Traditional market context</h1>
          <p className="text-sm text-textSecondary">DXY, yields, VIX, ETF flows, and the macro regime.</p>
        </div>
        <LastUpdated date={lastUpdated} label="Last snapshot" />
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader><h2 className="text-sm font-semibold">Macro Score</h2></CardHeader>
          <CardBody className="flex flex-col items-center gap-3 py-8">
            <div className={`text-7xl font-bold tabular-nums ${scoreColor}`}>
              {macroScore == null ? "—" : macroScore.toFixed(0)}
            </div>
            <div className="text-xs text-textSecondary">Range: -100 to +100</div>
            {score && (
              <div className="mt-2 flex flex-col items-center gap-1 text-xs text-textSecondary">
                <div>DXY: <span className="text-textPrimary">{score.dxy_trend}</span></div>
                <div>VIX: <span className="text-textPrimary">{score.vix_level}</span></div>
                <div>ETF flows: <span className="text-textPrimary">{score.etf_flows}</span></div>
                <Badge variant={score.risk_environment === "risk_on" ? "bullish" : score.risk_environment === "risk_off" ? "bearish" : "neutral"}>
                  {score.risk_environment.replace("_", " ")}
                </Badge>
              </div>
            )}
          </CardBody>
        </Card>

        <div className="lg:col-span-2">
          {sL ? <Skeleton className="h-64" /> : (
            <section className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3 md:gap-3">
              <MetricCard label="DXY" value={snap?.dxy ?? null} valueDecimals={2} />
              <MetricCard label="US 10Y" value={snap?.us10y ?? null} valueSuffix="%" valueDecimals={2} />
              <MetricCard label="VIX" value={snap?.vix ?? null} valueDecimals={2} />
              <MetricCard label="S&P 500" value={snap?.sp500 ?? null} valueDecimals={2} />
              <MetricCard label="BTC ETF Flows" value={snap?.btc_etf_flows_usd ?? null} valuePrefix="$" valueDecimals={0} />
              <MetricCard label="Gold" value={snap?.gold ?? null} valuePrefix="$" valueDecimals={2} />
            </section>
          )}
        </div>
      </div>

      <Card>
        <CardHeader><h2 className="text-sm font-semibold">Upcoming economic events (next 72h)</h2></CardHeader>
        <CardBody className="flex flex-col gap-2">
          {cL ? <Skeleton className="h-32" /> : (cal?.items ?? []).length === 0 ? (
            <p className="text-sm text-textSecondary">No scheduled events.</p>
          ) : (
            (cal?.items ?? []).map((e) => (
              <div key={e.id} className="flex flex-col gap-1 rounded-md border border-borderSubtle bg-bgElevated px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap items-center gap-2">
                  {e.impact && (
                    <Badge variant={e.impact === "high" ? "bearish" : e.impact === "medium" ? "warning" : "neutral"}>
                      {e.impact.toUpperCase()}
                    </Badge>
                  )}
                  <span className="font-semibold">{e.name}</span>
                  {e.country && <span className="text-xs text-textMuted">· {e.country}</span>}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-textSecondary sm:gap-3">
                  {e.forecast && <span>Forecast: <NumberDisplay value={Number(e.forecast)} decimals={2} /></span>}
                  {e.previous && <span>Prev: <NumberDisplay value={Number(e.previous)} decimals={2} /></span>}
                  <span className="text-textMuted">{new Date(e.scheduled_at).toLocaleString()}</span>
                </div>
              </div>
            ))
          )}
        </CardBody>
      </Card>
    </div>
  );
}
