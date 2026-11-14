import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/components/ui/cn";
import { xstrendApi, type XsTrendPortfolio, type XsTrendPosition, type XsTrendWeek } from "@/api/modules";

function pct(x: number | null | undefined, digits = 2): string {
  if (x == null) return "—";
  const s = Math.abs(x * 100).toFixed(digits);
  return `${Number(s) === 0 ? "" : x > 0 ? "+" : "−"}${s}%`;
}

function tone(x: number | null | undefined): string {
  if (x == null || x === 0) return "text-textSecondary";
  return x > 0 ? "text-profit" : "text-loss";
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

const VERDICT: Record<XsTrendPortfolio["stats"]["verdict"], { label: string; variant: "neutral" | "bullish" | "bearish" | "warning" }> = {
  running: { label: "Running", variant: "neutral" },
  pass: { label: "Passed", variant: "bullish" },
  ruled_out: { label: "Ruled out", variant: "bearish" },
  inconclusive: { label: "Inconclusive", variant: "warning" },
};

/** One cell per week of the test: the whole 104-week commitment at a glance. */
function WeekStrip({ weeks, target }: { weeks: XsTrendWeek[]; target: number }) {
  const worst = Math.max(0.01, ...weeks.map((w) => Math.abs(w.net ?? 0)));
  return (
    <ol
      className="grid grid-cols-[repeat(26,minmax(0,1fr))] gap-[3px]"
      aria-label={`${weeks.length} of ${target} weeks settled`}
    >
      {Array.from({ length: target }, (_, i) => {
        const w = weeks[i];
        const net = w?.net ?? null;
        return (
          <li
            key={i}
            title={w ? `Week of ${day(w.week_start)}: ${pct(net)}` : `Week ${i + 1}: not yet`}
            className={cn(
              "aspect-square rounded-[2px]",
              net == null ? "bg-bgElevated" : net >= 0 ? "bg-profit" : "bg-loss",
            )}
            style={net == null ? undefined : { opacity: 0.35 + 0.65 * Math.min(1, Math.abs(net) / worst) }}
          />
        );
      })}
    </ol>
  );
}

function Stat({ label, value, className, note }: { label: string; value: string; className?: string; note?: string }) {
  return (
    <div>
      <dt className="text-xs text-textMuted">{label}</dt>
      <dd className={cn("font-data text-lg tabular-nums", className)}>{value}</dd>
      {note && <dd className="text-xs text-textMuted">{note}</dd>}
    </div>
  );
}

function ForwardTest({ p }: { p: XsTrendPortfolio }) {
  const s = p.stats;
  const v = VERDICT[s.verdict];
  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">
          Forward test: week {Math.min(s.n, s.target)} of {s.target}
        </h2>
        <Badge variant={v.variant}>{v.label}</Badge>
      </CardHeader>
      <CardBody className="space-y-4">
        <WeekStrip weeks={p.weeks} target={s.target} />
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Stat
            label="Average week"
            value={pct(s.mean)}
            className={tone(s.mean)}
            note={s.low != null && s.high != null ? `likely ${pct(s.low)} to ${pct(s.high)}` : "needs 2 weeks"}
          />
          <Stat label="Total" value={pct(s.total)} className={tone(s.total)} note={`all coins: ${pct(s.mkt_total)}`} />
          <Stat label="Winning weeks" value={s.win_rate == null ? "—" : `${Math.round(s.win_rate * 100)}%`} />
          <Stat label="Worst drop" value={pct(s.drawdown)} className={tone(s.drawdown)} />
        </dl>
        <p className="max-w-[75ch] text-sm text-textSecondary">
          The verdict is read once, when week {s.target} settles. Until then these numbers describe the record and
          decide nothing: a year of weeks is too few to tell a real edge from luck.
          {s.top5_share != null && s.n >= 10 && (
            <> The five best weeks make up {Math.round(s.top5_share * 100)}% of the total.</>
          )}
        </p>
      </CardBody>
    </Card>
  );
}

function Leg({ title, rows, settled }: { title: string; rows: XsTrendPosition[]; settled: boolean }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1 text-sm font-semibold text-textSecondary">{title}</h3>
      <ul className="divide-y divide-borderSubtle">
        {rows.map((r) => (
          <li key={r.symbol} className="flex items-baseline justify-between gap-3 py-1.5">
            <a
              href={`https://www.bybit.com/trade/usdt/${r.symbol}`}
              target="_blank"
              rel="noreferrer"
              className="truncate font-data text-sm hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400"
            >
              {r.symbol.replace(/USDT$/, "")}
            </a>
            <span className={cn("font-data text-sm tabular-nums", settled ? tone((r.return ?? 0) * r.weight) : "text-textMuted")}>
              {settled ? pct(r.return) : `${Math.abs(r.weight * 100).toFixed(0)}%`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Basket({ week, nextRebalance }: { week: XsTrendWeek; nextRebalance: string }) {
  const settled = week.status === "settled";
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <h2 className="text-md font-semibold">
          {settled ? "Last basket" : "This week's basket"}, from {day(week.week_start)}
        </h2>
        <span className="text-sm text-textSecondary">
          {settled ? `Result ${pct(week.net)}` : `Held until ${day(nextRebalance)}, 00:00 UTC`}
        </span>
      </CardHeader>
      <CardBody className="grid gap-6 sm:grid-cols-2">
        <Leg title="Long: the strongest 10" rows={week.positions.filter((x) => x.weight > 0)} settled={settled} />
        <Leg title="Short: the weakest 10" rows={week.positions.filter((x) => x.weight < 0)} settled={settled} />
      </CardBody>
    </Card>
  );
}

function History({ weeks }: { weeks: XsTrendWeek[] }) {
  if (weeks.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">Settled weeks</h2>
      </CardHeader>
      <CardBody className="overflow-x-auto py-1">
        <table className="w-full min-w-[520px] text-sm">
          <thead>
            <tr className="text-left text-xs text-textMuted">
              <th className="py-2 font-normal">Week of</th>
              <th className="py-2 text-right font-normal">Result</th>
              <th className="py-2 text-right font-normal">Before costs</th>
              <th className="py-2 text-right font-normal">Trading costs</th>
              <th className="py-2 text-right font-normal">Funding paid</th>
              <th className="py-2 text-right font-normal">All coins</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-borderSubtle font-data tabular-nums">
            {[...weeks].reverse().map((w) => (
              <tr key={w.week_start}>
                <td className="py-1.5 font-sans">{day(w.week_start)}</td>
                <td className={cn("py-1.5 text-right", tone(w.net))}>{pct(w.net)}</td>
                <td className="py-1.5 text-right text-textSecondary">{pct(w.gross)}</td>
                <td className="py-1.5 text-right text-textSecondary">{pct(w.cost == null ? null : -w.cost)}</td>
                <td className="py-1.5 text-right text-textSecondary">{pct(w.funding == null ? null : -w.funding)}</td>
                <td className="py-1.5 text-right text-textSecondary">{pct(w.mkt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

function Rules({ p }: { p: XsTrendPortfolio }) {
  const r = p.rules;
  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">The rules</h2>
        <span className="font-data text-xs text-textMuted">version {r.version}</span>
      </CardHeader>
      <CardBody>
        <ul className="max-w-[75ch] list-disc space-y-1.5 pl-5 text-sm text-textSecondary">
          <li>Every Monday at 00:00 UTC, take the {r.universe} most-traded Bybit USDT perpetuals of the last 30 days.</li>
          <li>Rank them on their return over the last {r.horizons.join(", ").replace(/, (\d+)$/, " and $1")} days.</li>
          <li>
            Go long the top {r.per_leg} and short the bottom {r.per_leg}, each position {(50 / r.per_leg).toFixed(0)}% of
            capital. Hold for one week.
          </li>
          <li>Each trade is charged {r.cost_pct.toFixed(2)}% of its size, plus the funding Bybit actually settled.</li>
          <li>
            The rules are fixed for {p.stats.target} weeks from {day(r.first_week)}. In a backtest they made +0.82% a
            week, with about half of that from a handful of coins that pumped, so the backtest proves little by itself.
          </li>
        </ul>
      </CardBody>
    </Card>
  );
}

export default function XsTrendPage() {
  const q = useQuery({ queryKey: ["xstrend", "portfolio"], queryFn: xstrendApi.portfolio, refetchInterval: 5 * 60_000 });
  const p = q.data;

  if (q.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!p) {
    return (
      <Card>
        <CardBody>
          <p className="text-sm text-textSecondary">
            The portfolio could not be loaded. Check that the API is running, then reload the page.
          </p>
        </CardBody>
      </Card>
    );
  }

  const shown = p.current ?? p.weeks[p.weeks.length - 1] ?? null;
  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-bold">Weekly trend basket</h1>
        <p className="max-w-[70ch] text-sm text-textSecondary">
          A paper portfolio that is long the strongest coins and short the weakest, rebuilt every Monday. Nothing is
          sent to an exchange. It exists to find out, over two years, whether the idea survives outside a backtest.
        </p>
      </header>

      {!p.enabled && (
        <Card variant="danger">
          <CardBody>
            <p className="text-sm text-textSecondary">
              The weekly job is switched off. Set <code className="font-data">XSTREND_ENABLED=true</code> for the api and
              scheduler services, then restart them. Weeks missed in the meantime are rebuilt from the same public data.
            </p>
          </CardBody>
        </Card>
      )}

      <ForwardTest p={p} />

      {shown ? (
        <Basket week={shown} nextRebalance={p.next_rebalance} />
      ) : (
        <Card>
          <CardBody>
            <p className="text-sm text-textSecondary">
              No basket yet. The first one is built on {day(p.next_rebalance)} at 00:15 UTC.
            </p>
          </CardBody>
        </Card>
      )}

      <History weeks={p.weeks} />
      <Rules p={p} />
    </div>
  );
}
