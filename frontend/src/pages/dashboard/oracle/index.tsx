import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import {
  oracleApi,
  type LiveOracle,
  type OracleBoardRow,
  type OracleEvent,
  type OracleFollowup,
  type FollowupVerdict,
  type OracleOutcomeStats,
  type OraclePick,
  type OracleSignal,
} from "@/api/modules";

type TabKey = "picks" | "symbol" | "board" | "followup";

// Below this many measured signals a hit rate is noise — say "too few".
const MIN_SAMPLE = 20;

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
  const [tab, setTab] = useState<TabKey>("picks");
  const [symbol, setSymbol] = useState("BTCUSDT");

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">Oracle — Meta-signal aggregator</h1>
          <p className="text-sm text-textSecondary">
            Per-symbol confluence + a cross-market leaderboard of coins where multiple modules agree.
          </p>
        </div>
        <LiveIndicator />
      </header>

      <Tabs
        tabs={[
          { key: "picks", label: "Watchlist" },
          { key: "symbol", label: "Per-Symbol Analysis" },
          { key: "board", label: "Confluence Board" },
          { key: "followup", label: "Alert Follow-up" },
        ]}
        active={tab}
        onChange={(k) => setTab(k as TabKey)}
      />

      {tab === "picks" && (
        <TopPicksView onOpen={(sym) => { setSymbol(sym); setTab("symbol"); }} />
      )}
      {tab === "symbol" && <PerSymbolView key={symbol} initialSymbol={symbol} />}
      {tab === "board" && <ConfluenceBoardView />}
      {tab === "followup" && <FollowupView />}
    </div>
  );
}

// ---------- top picks view ----------

function tradeUrl(symbol: string, entry: number | null | undefined) {
  return `/riskcalc?symbol=${symbol}${entry ? `&entry=${entry}` : ""}`;
}

function pct(v: number | null, sign = false) {
  if (v === null) return "—";
  return `${sign && v > 0 ? "+" : ""}${v.toFixed(sign ? 2 : 0)}%`;
}

function countdown(iso: string) {
  const mins = Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 60_000));
  if (mins < 60) return `in ${mins}m`;
  const h = Math.floor(mins / 60);
  return h < 24 ? `in ${h}h ${mins % 60}m` : `in ${Math.floor(h / 24)}d ${h % 24}h`;
}

function EventsBar({ events, horizon, windowHours }: { events: OracleEvent[]; horizon: number; windowHours: number }) {
  const soon = (e: OracleEvent) => new Date(e.scheduled_at).getTime() - Date.now() < windowHours * 3_600_000;
  const alarm = events.some((e) => e.impact === "high" && soon(e));
  return (
    <div className={`flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-3 py-2 text-xs sm:text-sm ${alarm ? "border-loss/60 bg-loss/10" : "border-borderSubtle bg-bgElevated"}`}>
      <span className="font-semibold">{alarm ? "⚠ Event risk" : "Incoming events"}</span>
      {events.length === 0 ? (
        <span className="text-textSecondary">Nothing high or medium impact in the next {horizon}h.</span>
      ) : (
        events.map((e) => (
          <span key={e.name + e.scheduled_at} className={e.impact === "high" ? "text-textPrimary" : "text-textSecondary"} title={new Date(e.scheduled_at).toUTCString()}>
            <span className={e.impact === "high" ? "text-loss" : "text-warning"}>●</span> {e.name}{" "}
            <span className="text-textMuted">{countdown(e.scheduled_at)}</span>
          </span>
        ))
      )}
    </div>
  );
}

function TopPicksView({ onOpen }: { onOpen: (symbol: string) => void }) {
  const nav = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["oracle", "picks"],
    queryFn: () => oracleApi.picks(10),
    refetchInterval: 60_000,
  });
  const updated = data?.updated_at ? new Date(data.updated_at) : null;

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      {data ? <TrackRecord days={data.track_record.days} alerted={data.track_record.alerted} others={data.track_record.others} /> : <Skeleton className="h-14" />}
      {data && <EventsBar events={data.events} horizon={data.events_horizon_hours} windowHours={data.pick_window_hours} />}

      <Card>
        <CardHeader>
          <div className="flex w-full items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Where modules agree right now</h2>
            <LastUpdated date={updated} label="Snapshot" />
          </div>
        </CardHeader>
        <CardBody className="flex flex-col gap-2">
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : data?.stale ? (
            <p className="text-sm text-textSecondary">No snapshot yet — the scheduler refreshes every 3 min. Check back shortly.</p>
          ) : (data?.picks ?? []).length === 0 ? (
            <p className="text-sm text-textSecondary">Nothing has 3+ modules agreeing right now.</p>
          ) : (
            data!.picks.map((p, i) => (
              <PickRow key={p.symbol} rank={i + 1} pick={p} onOpen={() => onOpen(p.symbol)} onTrade={() => nav(tradeUrl(p.symbol, p.current_price))} />
            ))
          )}
          <p className="mt-1 text-xs text-textMuted">
            "Like this" = every measured Oracle signal in the same score band (±10) over the last {data?.similar_lookback_days ?? 30} days, judged at 4h.
          </p>
          <BoardTestNote />
        </CardBody>
      </Card>
    </div>
  );
}

function TrackRecord({ days, alerted, others }: { days: number; alerted: OracleOutcomeStats; others: OracleOutcomeStats }) {
  const judged = alerted.n >= MIN_SAMPLE && alerted.hit_4h_pct !== null && others.hit_4h_pct !== null;
  // Harness Eval A: alerts must beat the rest by 5+ points and on average move.
  const beating = judged
    && alerted.hit_4h_pct! - others.hit_4h_pct! >= 5
    && (alerted.avg_move_4h_pct ?? 0) > (others.avg_move_4h_pct ?? 0);
  const verdict = !judged
    ? { text: `Too few alerts to judge (n=${alerted.n})`, variant: "neutral" as const }
    : beating
      ? { text: "Alerts beating the rest", variant: "bullish" as const }
      : { text: "Alerts NOT beating the rest", variant: "warning" as const };

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-borderSubtle bg-bgElevated px-3 py-2 text-xs sm:text-sm">
      <span className="font-semibold">Last {days} days</span>
      <Badge variant={verdict.variant}>{verdict.text}</Badge>
      <span className="text-textSecondary">Alerts: <b className="text-textPrimary">{alerted.n}</b></span>
      <span className="text-textSecondary">
        Won 4h: <b className="text-textPrimary">{pct(alerted.hit_4h_pct)}</b> vs {pct(others.hit_4h_pct)} for the rest
      </span>
      <span className="text-textSecondary">
        Avg move: <b className="text-textPrimary">{pct(alerted.avg_move_4h_pct, true)}</b> vs {pct(others.avg_move_4h_pct, true)}
      </span>
    </div>
  );
}

function PickRow({ rank, pick, onOpen, onTrade }: { rank: number; pick: OraclePick; onOpen: () => void; onTrade: () => void }) {
  const long = pick.score > 0;
  const s = pick.similar;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-borderSubtle bg-bgElevated px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <span className="w-5 text-xs text-textMuted tabular-nums">{rank}</span>
        <button type="button" onClick={onOpen} className="font-semibold hover:underline" title="Open per-symbol analysis">
          {pick.symbol}
        </button>
        <span className={`text-xs font-bold ${long ? "text-profit" : "text-loss"}`}>{long ? "Bullish" : "Bearish"}</span>
        <span className={`text-sm font-bold tabular-nums ${long ? "text-profit" : "text-loss"}`}>{long ? "+" : ""}{pick.score}</span>
        <div className="flex flex-wrap gap-1">
          {pick.modules.map((m) => (
            <span key={m.name} className="rounded bg-bgSecondary px-2 py-0.5 text-[10px] capitalize text-textSecondary">{m.name}</span>
          ))}
        </div>
        {pick.events_in_window.length > 0 && (
          <span className="rounded bg-loss/15 px-2 py-0.5 text-[10px] font-semibold text-loss" title={pick.events_in_window.map((e) => e.name).join(", ")}>
            ⚠ {pick.events_in_window[0].name} {countdown(pick.events_in_window[0].scheduled_at)}
            {pick.events_in_window.length > 1 ? ` +${pick.events_in_window.length - 1}` : ""}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 text-xs">
        <span className="text-textMuted" title="Measured Oracle signals in the same score band">
          {s.n >= MIN_SAMPLE ? (
            <>Like this: <b className="text-textPrimary">{pct(s.hit_4h_pct)}</b> won · {pct(s.avg_move_4h_pct, true)} · n={s.n}</>
          ) : (
            <>Like this: too few (n={s.n})</>
          )}
        </span>
        <Button size="sm" variant="secondary" onClick={onTrade}>Trade</Button>
      </div>
    </div>
  );
}

// ---------- alert follow-up ----------

const VERDICT_META: Record<FollowupVerdict, { cls: string; icon: string; title: string }> = {
  right: { cls: "text-profit", icon: "✅", title: "Moved in the called direction by more than the noise bar" },
  wrong: { cls: "text-loss", icon: "❌", title: "Moved against the call by more than the noise bar" },
  flat: { cls: "text-textMuted", icon: "➖", title: "Moved less than the noise bar either way — no real move" },
};

function FollowupView() {
  const PAGE = 50;
  const q = useInfiniteQuery({
    queryKey: ["oracle", "followups"],
    queryFn: ({ pageParam }) => oracleApi.followups(PAGE, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.items.length < PAGE ? undefined : pages.length * PAGE),
    refetchInterval: 60_000,
  });
  const isLoading = q.isLoading;
  const first = q.data?.pages[0];
  const data = first && { items: q.data!.pages.flatMap((p) => p.items) };
  const rec = first?.record;
  const all = first?.record_all_time;

  return (
    <Card>
      <CardHeader><h2 className="text-sm font-semibold">What happened after each alert</h2></CardHeader>
      <CardBody className="flex flex-col gap-3">
        <p className="text-xs text-textSecondary sm:text-sm">
          Every alert you got on Telegram, then price 1h, 4h and 24h later, measured <i>in the called direction</i> (+ = the call was right).
          Under ±0.2% (±0.5% for strong calls) counts as no real move. "Best / worst 1h" is how far it ran for and against you in the first hour —
          a call can close right but stop you out first. You also get a Telegram follow-up at 4h and 24h.
        </p>
        {rec && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-md border border-borderSubtle bg-bgElevated px-3 py-2 text-xs sm:text-sm">
            <span className="font-semibold">Last {rec.days} days at 4h</span>
            {rec.n < MIN_SAMPLE ? (
              <Badge variant="neutral">Too few alerts to judge (n={rec.n})</Badge>
            ) : (
              <Badge variant={(rec.hit_pct ?? 0) > 50 ? "bullish" : "warning"}>{rec.hit_pct}% right</Badge>
            )}
            <span className="text-textSecondary">Avg move: <b className="text-textPrimary">{pct(rec.avg_move_pct, true)}</b></span>
            {all && all.n > 0 && (
              <span className="text-textSecondary">
                All time: <b className="text-textPrimary">{pct(all.hit_pct)}</b> right · {pct(all.avg_move_pct, true)} · n={all.n}
              </span>
            )}
          </div>
        )}
        {isLoading ? (
          <Skeleton className="h-40" />
        ) : !data?.items.length ? (
          <p className="text-sm text-textSecondary">No alerts yet. The next Telegram alert will show up here and be tracked.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-xs sm:text-sm">
              <thead className="text-left text-textMuted">
                <tr>
                  <th className="py-1 pr-3 font-medium">When</th>
                  <th className="py-1 pr-3 font-medium">Call</th>
                  <th className="py-1 pr-3 text-right font-medium">1h</th>
                  <th className="py-1 pr-3 text-right font-medium">4h</th>
                  <th className="py-1 pr-3 text-right font-medium">24h</th>
                  <th className="py-1 text-right font-medium">Best / worst 1h</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((f) => <FollowupRow key={f.id} f={f} />)}
              </tbody>
            </table>
            {q.hasNextPage && (
              <Button size="sm" variant="secondary" className="mt-2" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
                {q.isFetchingNextPage ? "Loading…" : "Load older alerts"}
              </Button>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function FollowupRow({ f }: { f: OracleFollowup }) {
  const long = f.direction === "long";
  const [best, worst] = long ? [f.mfe_1h_pct, f.mae_1h_pct] : [f.mae_1h_pct, f.mfe_1h_pct];
  return (
    <tr className="border-t border-borderSubtle">
      <td className="py-1.5 pr-3 text-textSecondary tabular-nums">
        {new Date(f.signal_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
      </td>
      <td className="py-1.5 pr-3">
        <span className="font-semibold">{f.symbol}</span>{" "}
        <span className={`text-xs font-bold ${long ? "text-profit" : "text-loss"}`}>{long ? "LONG" : "SHORT"}</span>{" "}
        <span className="text-[10px] text-textMuted">{f.source === "board" ? "board" : "oracle"} @ {f.price_at_signal ?? "—"}</span>
      </td>
      {(["1h", "4h", "24h"] as const).map((h) => {
        const v = f.horizons[h];
        if (!v) return <td key={h} className="py-1.5 pr-3 text-right text-textMuted">pending</td>;
        const m = VERDICT_META[v.verdict];
        return (
          <td key={h} className={`py-1.5 pr-3 text-right tabular-nums ${m.cls}`} title={m.title}>
            {m.icon} {pct(v.move_pct, true)}
          </td>
        );
      })}
      <td className="py-1.5 text-right tabular-nums text-textSecondary">
        {best === null || worst === null ? "—" : <><span className="text-profit">+{best.toFixed(2)}%</span> / <span className="text-loss">-{worst.toFixed(2)}%</span></>}
      </td>
    </tr>
  );
}

// ---------- per-symbol view ----------

function PerSymbolView({ initialSymbol }: { initialSymbol: string }) {
  const nav = useNavigate();
  const [symbol, setSymbol] = useState(initialSymbol);
  const [pending, setPending] = useState(symbol);

  const { data: live, isLoading: liveLoading } = useQuery({
    queryKey: ["oracle", "live", symbol],
    queryFn: () => oracleApi.live(symbol),
    refetchInterval: 30000,
  });
  const { data: signals } = useQuery({
    queryKey: ["oracle", "signals"],
    queryFn: () => oracleApi.signals({ limit: 20 }),
  });

  const lastUpdated = useMemo(() => {
    const items = signals?.items ?? [];
    return items.length ? new Date(items[0].signal_at) : null;
  }, [signals]);

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <div className="flex items-center justify-end">
        <LastUpdated date={lastUpdated} label="Last signal" />
      </div>

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); setSymbol(pending.toUpperCase()); }}
      >
        <Input label="Symbol" value={pending} onChange={(e) => setPending(e.target.value)} className="w-full sm:w-48" />
        <Button type="submit" variant="secondary">Analyze</Button>
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

      <Card>
        <CardHeader><h2 className="text-sm font-semibold">Recent signals</h2></CardHeader>
        <CardBody className="flex flex-col gap-2">
          {(signals?.items ?? []).length === 0 ? (
            <p className="text-sm text-textSecondary">No signals generated yet.</p>
          ) : (
            (signals?.items ?? []).map((s) => (
              <SignalRow key={s.id} s={s} onTrade={() => nav(tradeUrl(s.symbol, s.entry_price))} />
            ))
          )}
        </CardBody>
      </Card>
    </div>
  );
}

// ---------- confluence board view ----------

/** Result of docs/research/2026-11-14-oracle-confluence-board.md, shown wherever board rows are listed. */
function BoardTestNote() {
  return (
    <p className="mt-2 max-w-[75ch] text-xs text-textSecondary">
      These are places to look, not trades. Tested on 1,554 past board entries (Sep to Nov 2026): 49.5% moved the
      agreed way over the next 4 hours and the typical one lost money after costs.
    </p>
  );
}

function ConfluenceBoardView() {
  const [minModules, setMinModules] = useState(3);
  const [minScore, setMinScore] = useState(0);

  const { data, isLoading } = useQuery({
    queryKey: ["oracle", "board", { minModules, minScore }],
    queryFn: () => oracleApi.board({ min_modules: minModules, min_score: minScore }),
    refetchInterval: 60_000,
  });

  const updated = data?.updated_at ? new Date(data.updated_at) : null;

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between w-full">
            <div className="flex items-end gap-3">
              <NumericFilter
                label="Min modules agreeing"
                value={minModules}
                onChange={setMinModules}
                min={2}
                max={8}
                step={1}
              />
              <NumericFilter
                label="Min |score|"
                value={minScore}
                onChange={setMinScore}
                min={0}
                max={100}
                step={5}
              />
            </div>
            <LastUpdated date={updated} label="Snapshot" />
          </div>
        </CardHeader>
        <CardBody>
          {data?.stale ? (
            <p className="text-sm text-textSecondary">
              No snapshot yet — the scheduler refreshes every 3 min. Check back shortly.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <BoardColumn
                title="Bullish confluence"
                tone="bullish"
                rows={data?.bullish ?? []}
                loading={isLoading}
              />
              <BoardColumn
                title="Bearish confluence"
                tone="bearish"
                rows={data?.bearish ?? []}
                loading={isLoading}
              />
            </div>
          )}
          <p className="mt-3 text-xs text-textMuted">
            Universe: top {data?.universe_size ?? 50} perps by 24h volume. A symbol qualifies when ≥{data?.min_modules ?? 3}{" "}
            modules signal the same direction with intensity {">"} 0.3.
          </p>
          <BoardTestNote />
        </CardBody>
      </Card>
    </div>
  );
}

function BoardColumn({
  title, tone, rows, loading,
}: {
  title: string;
  tone: "bullish" | "bearish";
  rows: OracleBoardRow[];
  loading: boolean;
}) {
  if (loading) return <Skeleton className="h-48" />;
  if (rows.length === 0) {
    return (
      <div>
        <h3 className={`mb-2 text-sm font-semibold ${tone === "bullish" ? "text-profit" : "text-loss"}`}>
          {title}
        </h3>
        <p className="text-sm text-textSecondary">Nothing crossing the bar.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <h3 className={`text-sm font-semibold ${tone === "bullish" ? "text-profit" : "text-loss"}`}>
        {title} <span className="ml-1 font-normal text-textMuted">({rows.length})</span>
      </h3>
      {rows.map((r) => <BoardRow key={r.symbol} row={r} tone={tone} />)}
    </div>
  );
}

function BoardRow({ row, tone }: { row: OracleBoardRow; tone: "bullish" | "bearish" }) {
  const scoreColor = tone === "bullish" ? "text-profit" : "text-loss";
  return (
    <div className="rounded-md border border-borderSubtle bg-bgElevated px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold tabular-nums">{row.symbol}</span>
          <Badge variant="neutral" className="text-[10px]">
            {row.agreeing_count}/{row.confluence_count} modules
          </Badge>
          <Badge variant={row.confidence === "high" ? "bullish" : row.confidence === "medium" ? "warning" : "neutral"} className="text-[10px]">
            {row.confidence}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-sm font-bold tabular-nums ${scoreColor}`}>
            {row.score > 0 ? "+" : ""}{row.score}
          </span>
          {row.current_price ? (
            <span className="text-xs text-textMuted">
              <NumberDisplay value={row.current_price} decimals={4} prefix="$" />
            </span>
          ) : null}
        </div>
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {row.modules.map((m) => (
          <span
            key={m.name}
            className="rounded bg-bgSecondary px-2 py-0.5 text-[10px] capitalize text-textSecondary"
          >
            {m.name}
            <span className="ml-1 text-textMuted">{m.intensity.toFixed(2)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function NumericFilter({
  label, value, onChange, min, max, step,
}: { label: string; value: number; onChange: (v: number) => void; min: number; max: number; step: number }) {
  return (
    <div className="w-32">
      <label className="mb-1.5 block text-xs font-medium text-textSecondary">{label}</label>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          onChange(Number.isFinite(n) ? n : 0);
        }}
        className="w-full rounded-md border border-borderDefault bg-bgSecondary px-3 h-10 text-sm focus:border-borderStrong focus:outline-none"
      />
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
          <div key={mod} className="grid grid-cols-[80px_1fr_50px] items-center gap-2 text-xs sm:grid-cols-[120px_1fr_80px] sm:gap-3">
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
    <div className="flex flex-col gap-2 rounded-md border border-borderSubtle bg-bgElevated px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <span className="font-semibold">{s.symbol}</span>
        <span className={`text-xs font-semibold ${meta.color}`}>{meta.label}</span>
        <Badge variant={s.confidence === "high" ? "bullish" : s.confidence === "medium" ? "warning" : "neutral"}>
          {s.confidence}
        </Badge>
        <span className="text-xs text-textMuted">{s.confluence_count} modules</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs sm:gap-3">
        <span className="font-semibold tabular-nums">{s.score.toFixed(0)}</span>
        {s.entry_price && <span className="text-textMuted">E: <NumberDisplay value={s.entry_price} decimals={4} /></span>}
        {s.rr_ratio && <span className="text-textMuted">R:R {s.rr_ratio.toFixed(2)}</span>}
        <span className="text-textMuted">{new Date(s.signal_at).toLocaleTimeString()}</span>
        <Button size="sm" variant="secondary" onClick={onTrade}>Trade</Button>
      </div>
    </div>
  );
}
