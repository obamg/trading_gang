import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricCard } from "@/components/ui/MetricCard";
import { Tabs } from "@/components/ui/Tabs";
import { Table, type Column } from "@/components/ui/Table";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { performanceApi, type PerfSnap } from "@/api/modules";

type Period = "7d" | "30d" | "all";
type Breakdown = "setup" | "symbol" | "time";

export default function PerformancePage() {
  const [period, setPeriod] = useState<Period>("30d");
  const [isPaper, setIsPaper] = useState(false);
  const [breakdown, setBreakdown] = useState<Breakdown>("setup");

  const { data: overview, isLoading: oL } = useQuery({
    queryKey: ["perf", "overview", isPaper],
    queryFn: () => performanceApi.overview(isPaper),
  });
  const { data: equity, isLoading: eL } = useQuery({
    queryKey: ["perf", "equity", isPaper],
    queryFn: () => performanceApi.equityCurve(90, isPaper),
  });
  const { data: sigPerf } = useQuery({ queryKey: ["perf", "signals"], queryFn: () => performanceApi.signals("oracle") });

  const snap: PerfSnap | undefined = overview?.[period];

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">PerformanceCore — Trading analytics</h1>
          <p className="text-sm text-textSecondary">Win rate, expectancy, equity curve, signal accuracy.</p>
        </div>
        <label className="flex items-center gap-2 text-sm text-textSecondary">
          <input type="checkbox" checked={isPaper} onChange={(e) => setIsPaper(e.target.checked)} />
          Paper only
        </label>
      </header>

      <Tabs
        tabs={[
          { key: "7d", label: "7 days" },
          { key: "30d", label: "30 days" },
          { key: "all", label: "All time" },
        ]}
        active={period}
        onChange={(k) => setPeriod(k as Period)}
      />

      {oL ? <Skeleton className="h-28" /> : (
        <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 md:gap-3 lg:grid-cols-6">
          <MetricCard label="Total trades" value={snap?.total_trades ?? null} valueDecimals={0} />
          <MetricCard label="Win rate" value={snap?.win_rate ?? null} valueSuffix="%" valueDecimals={1} />
          <MetricCard label="Expectancy" value={snap?.expectancy ?? null} valueSuffix="R" valueDecimals={2} />
          <MetricCard label="Profit factor" value={snap?.profit_factor ?? null} valueDecimals={2} />
          <MetricCard label="Net P&L" value={snap?.net_pnl_usd ?? null} valuePrefix="$" valueDecimals={0} />
          <MetricCard label="Max drawdown" value={snap?.max_drawdown_pct ?? null} valueSuffix="%" valueDecimals={1} />
        </section>
      )}

      <Card>
        <CardHeader><h2 className="text-sm font-semibold">Equity curve (90d)</h2></CardHeader>
        <CardBody>
          {eL ? <Skeleton className="h-48" /> : <EquitySvg points={equity?.points ?? []} />}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <Tabs
            tabs={[
              { key: "setup", label: "By setup" },
              { key: "symbol", label: "By symbol" },
              { key: "time", label: "By time" },
            ]}
            active={breakdown}
            onChange={(k) => setBreakdown(k as Breakdown)}
          />
        </CardHeader>
        <CardBody className="p-0">
          {breakdown === "setup" && <BySetup />}
          {breakdown === "symbol" && <BySymbol />}
          {breakdown === "time" && <ByTime />}
        </CardBody>
      </Card>

      <Card>
        <CardHeader><h2 className="text-sm font-semibold">Oracle signal accuracy</h2></CardHeader>
        <CardBody>
          {!sigPerf ? <Skeleton className="h-16" /> : (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 md:gap-3">
              <MetricCard label="Total signals" value={sigPerf.total_signals} valueDecimals={0} />
              <MetricCard label="1h accuracy" value={sigPerf.accuracy_1h_pct} valueSuffix="%" valueDecimals={1} />
              <MetricCard label="4h accuracy" value={sigPerf.accuracy_4h_pct} valueSuffix="%" valueDecimals={1} />
              <MetricCard label="Avg 4h move" value={sigPerf.avg_move_4h_pct} valueSuffix="%" valueDecimals={2} />
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function EquitySvg({ points }: { points: { t: string; equity: number }[] }) {
  if (points.length < 2) return <p className="text-sm text-textSecondary">Not enough data for an equity curve yet.</p>;
  const w = 800, h = 200, pad = 20;
  const ys = points.map((p) => p.equity);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const rangeY = maxY - minY || 1;
  const path = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (w - 2 * pad);
    const y = h - pad - ((p.equity - minY) / rangeY) * (h - 2 * pad);
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const positive = points[points.length - 1].equity >= points[0].equity;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-48 w-full">
      <path d={path} fill="none" stroke={positive ? "var(--tw-profit, #22c55e)" : "var(--tw-loss, #ef4444)"} strokeWidth="2" className={positive ? "text-profit" : "text-loss"} />
    </svg>
  );
}

function BySetup() {
  const { data, isLoading } = useQuery({ queryKey: ["perf", "by-setup"], queryFn: performanceApi.bySetup });
  if (isLoading) return <Skeleton className="m-4 h-48" />;
  type R = NonNullable<typeof data>["items"][number];
  const columns: Column<R>[] = [
    { key: "setup", header: "Setup", accessor: (r) => <span className="font-semibold">{r.setup}</span> },
    { key: "n", header: "Trades", accessor: (r) => r.total_trades, align: "right", sortValue: (r) => r.total_trades },
    { key: "wr", header: "Win rate", accessor: (r) => r.win_rate != null ? `${r.win_rate.toFixed(1)}%` : "—", align: "right" },
    { key: "r", header: "Avg R", accessor: (r) => r.avg_r_multiple != null ? <NumberDisplay value={r.avg_r_multiple} decimals={2} suffix="R" sign /> : "—", align: "right" },
    { key: "pnl", header: "Net P&L", accessor: (r) => <NumberDisplay value={r.net_pnl_usd ?? 0} decimals={0} prefix="$" sign colored />, align: "right", sortValue: (r) => r.net_pnl_usd ?? 0 },
  ];
  return <Table columns={columns} rows={data?.items ?? []} rowKey={(r) => r.setup} emptyMessage="No setups recorded." />;
}

function BySymbol() {
  const { data, isLoading } = useQuery({ queryKey: ["perf", "by-symbol"], queryFn: () => performanceApi.bySymbol(20) });
  if (isLoading) return <Skeleton className="m-4 h-48" />;
  type R = NonNullable<typeof data>["items"][number];
  const columns: Column<R>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <span className="font-semibold">{r.symbol}</span> },
    { key: "n", header: "Trades", accessor: (r) => r.total_trades, align: "right" },
    { key: "pnl", header: "Net P&L", accessor: (r) => <NumberDisplay value={r.net_pnl_usd} decimals={0} prefix="$" sign colored />, align: "right", sortValue: (r) => r.net_pnl_usd },
  ];
  return <Table columns={columns} rows={data?.items ?? []} rowKey={(r) => r.symbol} emptyMessage="No data." />;
}

function ByTime() {
  const { data, isLoading } = useQuery({ queryKey: ["perf", "by-time"], queryFn: performanceApi.byTime });
  if (isLoading) return <Skeleton className="m-4 h-48" />;
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return (
    <div className="grid grid-cols-1 gap-6 p-4 md:grid-cols-2">
      <div>
        <div className="mb-2 text-xs font-semibold text-textSecondary">By hour of day</div>
        <div className="flex items-end gap-0.5 h-32">
          {Array.from({ length: 24 }, (_, h) => {
            const row = (data?.by_hour ?? []).find((r) => r.hour === h);
            const pnl = row?.net_pnl_usd ?? 0;
            const max = Math.max(1, ...(data?.by_hour ?? []).map((r) => Math.abs(r.net_pnl_usd)));
            const height = (Math.abs(pnl) / max) * 100;
            return (
              <div key={h} className="flex flex-1 flex-col items-center gap-1" title={`${h}:00 · $${pnl.toFixed(0)}`}>
                <div className={`w-full ${pnl >= 0 ? "bg-profit/60" : "bg-loss/60"}`} style={{ height: `${height}%` }} />
                <div className="text-[10px] text-textMuted">{h}</div>
              </div>
            );
          })}
        </div>
      </div>
      <div>
        <div className="mb-2 text-xs font-semibold text-textSecondary">By day of week</div>
        <div className="flex flex-col gap-1">
          {days.map((d, i) => {
            const row = (data?.by_day_of_week ?? []).find((r) => r.day_of_week === i);
            const pnl = row?.net_pnl_usd ?? 0;
            return (
              <div key={d} className="flex items-center justify-between rounded bg-bgElevated px-2 py-1 text-xs">
                <span>{d}</span>
                <span className={pnl >= 0 ? "text-profit" : "text-loss"}>
                  <NumberDisplay value={pnl} decimals={0} prefix="$" sign />
                  <span className="ml-2 text-textMuted">({row?.trades ?? 0} trades)</span>
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
