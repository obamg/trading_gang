import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { MetricCard } from "@/components/ui/MetricCard";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { oracleApi, type LiveOracle, type OracleSignal } from "@/api/modules";

const RECOMMENDATION_META: Record<string, { color: string; label: string }> = {
  strong_long: { color: "text-profit", label: "STRONG LONG" },
  long: { color: "text-profit", label: "LONG" },
  watch_long: { color: "text-profit/70", label: "WATCH LONG" },
  neutral: { color: "text-textSecondary", label: "NEUTRAL" },
  watch_short: { color: "text-loss/70", label: "WATCH SHORT" },
  short: { color: "text-loss", label: "SHORT" },
  strong_short: { color: "text-loss", label: "STRONG SHORT" },
};

export default function OraclePage() {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [pending, setPending] = useState(symbol);

  const { data: live, isLoading: liveLoading, refetch: refetchLive } = useQuery({
    queryKey: ["oracle", "live", symbol],
    queryFn: () => oracleApi.live(symbol),
    refetchInterval: 30000,
  });
  const { data: signals } = useQuery({
    queryKey: ["oracle", "signals"],
    queryFn: () => oracleApi.signals({ limit: 20 }),
  });
  const { data: perf } = useQuery({ queryKey: ["oracle", "perf"], queryFn: oracleApi.performance });
  const gen = useMutation({
    mutationFn: () => oracleApi.generate(symbol, true),
    onSuccess: () => refetchLive(),
  });

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Oracle — Meta-signal aggregator</h1>
          <p className="text-sm text-textSecondary">6-module confluence with direction, confidence, and trade plan.</p>
        </div>
        <LiveIndicator />
      </header>

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); setSymbol(pending.toUpperCase()); }}
      >
        <Input label="Symbol" value={pending} onChange={(e) => setPending(e.target.value)} className="w-48" />
        <Button type="submit" variant="secondary">Analyze</Button>
        <Button type="button" onClick={() => gen.mutate()} disabled={gen.isPending}>
          {gen.isPending ? "Generating…" : "Generate signal"}
        </Button>
      </form>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader><h2 className="text-sm font-semibold">{symbol} — current score</h2></CardHeader>
          <CardBody>
            {liveLoading ? <Skeleton className="h-48" /> : live ? <ScoreGauge live={live} /> : <p className="text-sm text-textSecondary">No live data.</p>}
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><h2 className="text-sm font-semibold">Module breakdown</h2></CardHeader>
          <CardBody>
            {liveLoading ? <Skeleton className="h-48" /> : live ? <ModuleBars live={live} /> : <p className="text-sm text-textSecondary">—</p>}
          </CardBody>
        </Card>
      </div>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Signals generated" value={perf?.total_signals ?? null} valueDecimals={0} />
        <MetricCard label="1h accuracy" value={perf?.accuracy_1h_pct ?? null} valueSuffix="%" valueDecimals={1} />
        <MetricCard label="4h accuracy" value={perf?.accuracy_4h_pct ?? null} valueSuffix="%" valueDecimals={1} />
        <MetricCard label="Measured (4h)" value={perf?.measured_4h ?? null} valueDecimals={0} />
      </section>

      <Card>
        <CardHeader><h2 className="text-sm font-semibold">Recent signals</h2></CardHeader>
        <CardBody className="flex flex-col gap-2">
          {(signals?.items ?? []).length === 0 ? (
            <p className="text-sm text-textSecondary">No signals generated yet.</p>
          ) : (
            (signals?.items ?? []).map((s) => (
              <SignalRow key={s.id} s={s} onTrade={() => nav(`/riskcalc?symbol=${s.symbol}${s.entry_price ? `&entry=${s.entry_price}` : ""}`)} />
            ))
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function ScoreGauge({ live }: { live: LiveOracle }) {
  const meta = RECOMMENDATION_META[live.recommendation] ?? RECOMMENDATION_META.neutral;
  // Semicircle arc: -100 → 180°, 0 → 90°, +100 → 0°
  const clamp = Math.max(-100, Math.min(100, live.score));
  const angle = 180 - ((clamp + 100) / 200) * 180;
  const rad = (angle * Math.PI) / 180;
  const cx = 100, cy = 100, r = 80;
  const x = cx + r * Math.cos(rad);
  const y = cy - r * Math.sin(rad);
  const large = 0;
  const startX = cx - r, startY = cy;
  const scoreColor = clamp > 0 ? "#22c55e" : clamp < 0 ? "#ef4444" : "#94a3b8";

  return (
    <div className="flex flex-col items-center gap-3">
      <svg viewBox="0 0 200 120" className="h-40 w-full">
        <path d={`M ${startX} ${startY} A ${r} ${r} 0 ${large} 1 ${cx + r} ${cy}`} fill="none" stroke="#334155" strokeWidth="10" />
        <path d={`M ${startX} ${startY} A ${r} ${r} 0 0 1 ${x.toFixed(2)} ${y.toFixed(2)}`} fill="none" stroke={scoreColor} strokeWidth="10" strokeLinecap="round" />
        <text x="100" y="95" textAnchor="middle" fontSize="28" fontWeight="700" fill={scoreColor}>{clamp.toFixed(0)}</text>
      </svg>
      <div className={`text-lg font-semibold ${meta.color}`}>{meta.label}</div>
      <div className="flex items-center gap-3 text-xs text-textSecondary">
        <Badge variant={live.confidence === "high" ? "bullish" : live.confidence === "medium" ? "warning" : "neutral"}>
          {live.confidence.toUpperCase()} confidence
        </Badge>
        <span>{live.confluence_count} modules agree</span>
      </div>
      <div className="text-xs text-textMuted">Price: <NumberDisplay value={live.current_price} decimals={4} /></div>
      {live.macro_context.risk_environment && (
        <div className="text-xs text-textMuted">Macro: {live.macro_context.risk_environment.replace("_", " ")} · VIX {live.macro_context.vix_level ?? "—"}</div>
      )}
    </div>
  );
}

function ModuleBars({ live }: { live: LiveOracle }) {
  const entries = useMemo(() => Object.entries(live.signals_breakdown), [live]);
  return (
    <div className="flex flex-col gap-2">
      {entries.map(([mod, s]) => {
        const contrib = s.contribution;
        const width = Math.min(100, Math.abs(contrib) * 2);
        const color = contrib > 0 ? "bg-profit/60" : contrib < 0 ? "bg-loss/60" : "bg-borderSubtle";
        return (
          <div key={mod} className="grid grid-cols-[120px_1fr_80px] items-center gap-3 text-xs">
            <span className="font-semibold capitalize">{mod}</span>
            <div className="relative h-4 rounded bg-bgElevated overflow-hidden">
              <div className={`h-full ${contrib >= 0 ? "ml-1/2" : "mr-1/2"}`} style={{
                width: `${width / 2}%`,
                marginLeft: contrib >= 0 ? "50%" : `${50 - width / 2}%`,
              }}>
                <div className={`h-full ${color}`} style={{ width: "100%" }} />
              </div>
              <div className="absolute inset-y-0 left-1/2 w-px bg-borderStrong" />
            </div>
            <span className="text-right tabular-nums text-textMuted">
              <NumberDisplay value={contrib} decimals={1} sign />
            </span>
          </div>
        );
      })}
    </div>
  );
}

function SignalRow({ s, onTrade }: { s: OracleSignal; onTrade: () => void }) {
  const meta = RECOMMENDATION_META[s.recommendation] ?? RECOMMENDATION_META.neutral;
  return (
    <div className="flex items-center justify-between rounded-md border border-borderSubtle bg-bgElevated px-3 py-2">
      <div className="flex items-center gap-3">
        <span className="font-semibold">{s.symbol}</span>
        <span className={`text-xs font-semibold ${meta.color}`}>{meta.label}</span>
        <Badge variant={s.confidence === "high" ? "bullish" : s.confidence === "medium" ? "warning" : "neutral"}>
          {s.confidence}
        </Badge>
        <span className="text-xs text-textMuted">{s.confluence_count} modules</span>
      </div>
      <div className="flex items-center gap-3 text-xs">
        <span className="font-semibold tabular-nums">{s.score.toFixed(0)}</span>
        {s.entry_price && <span className="text-textMuted">E: <NumberDisplay value={s.entry_price} decimals={4} /></span>}
        {s.rr_ratio && <span className="text-textMuted">R:R {s.rr_ratio.toFixed(2)}</span>}
        <span className="text-textMuted">{new Date(s.signal_at).toLocaleTimeString()}</span>
        <Button size="sm" variant="secondary" onClick={onTrade}>Trade</Button>
      </div>
    </div>
  );
}
