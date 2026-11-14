import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { cn } from "@/components/ui/cn";
import {
  daytradeApi,
  type DayTradeDesk,
  type DayTradePlan,
  type DayTradePlaybook,
  type DayTradeSetup,
} from "@/api/modules";

const ACCENT = "#D946EF";
const SESSION_LABEL: Record<string, string> = {
  "00:00@UTC": "Daily open",
  "09:30@America/New_York": "US open",
};
/** "09:30@America/New_York" -> "09:30 New York" */
function sessionTime(spec: string): string {
  const [hhmm, tz = "UTC"] = spec.split("@");
  return `${hhmm} ${tz.split("/").pop()?.replace("_", " ")}`;
}

// ---------- small helpers ----------

function px(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { maximumSignificantDigits: 7 });
}

function signedR(r: number | null | undefined): string {
  if (r == null) return "—";
  return `${r >= 0 ? "+" : ""}${r.toFixed(2)}R`;
}

/** "27:53" under an hour, "3h 12m" above. */
function countdown(ms: number): string {
  if (ms <= 0) return "now";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function clamp(v: number, lo = 0, hi = 100): number {
  return Math.min(hi, Math.max(lo, v));
}

function tradeUrl(exchange: string, symbol: string): string {
  return exchange === "binance"
    ? `https://www.binance.com/en/futures/${symbol}`
    : `https://www.bybit.com/trade/usdt/${symbol}`;
}

/** One clock for the whole page, so every countdown ticks together. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

interface Sizer {
  account: number;
  riskPct: number;
}

function useSizer(defaults: Sizer): [Sizer, (s: Sizer) => void] {
  const [sizer, setSizer] = useState<Sizer>(() => {
    try {
      const raw = localStorage.getItem("daytrade.sizer");
      if (raw) return { ...defaults, ...JSON.parse(raw) };
    } catch {
      /* private mode or bad JSON: fall through to defaults */
    }
    return defaults;
  });
  const save = (s: Sizer) => {
    setSizer(s);
    try {
      localStorage.setItem("daytrade.sizer", JSON.stringify(s));
    } catch {
      /* not persisted; the inputs still work for this visit */
    }
  };
  return [sizer, save];
}

/** Same arithmetic as the backend sizing: lose `riskPct` of the account at the stop, fees included. */
function sizePosition(plan: DayTradePlan, sizer: Sizer, pb: DayTradePlaybook) {
  const riskUsd = (sizer.account * sizer.riskPct) / 100;
  const perUnit = plan.stop_pct / 100 + pb.friction_pct / 100;
  let notional = perUnit > 0 ? riskUsd / perUnit : 0;
  const capped = notional > sizer.account * pb.max_leverage;
  if (capped) notional = sizer.account * pb.max_leverage;
  return {
    riskUsd: capped ? notional * perUnit : riskUsd,
    notional,
    qty: plan.entry > 0 ? notional / plan.entry : 0,
    leverage: sizer.account > 0 ? notional / sizer.account : 0,
    capped,
  };
}

// ---------- the range ladder: the page's one signature element ----------

/**
 * A price axis (low on the left) with the opening range as the middle third.
 * Left of it is where a short triggers, right of it a long. The marker is the
 * live price; it glides between polls so you watch it approach an edge.
 */
function RangeLadder({ setup, delay }: { setup: DayTradeSetup; delay: number }) {
  const { range_high: hi, range_low: lo, last_price: price } = setup;
  if (hi == null || lo == null || hi <= lo) {
    return <div className="h-9 rounded-md bg-bgSecondary" aria-hidden />;
  }
  const width = hi - lo;
  const pos = price == null ? null : clamp(((price - (lo - width)) / (3 * width)) * 100);
  const forming = setup.status === "forming";
  const dead = setup.status === "expired" || setup.status === "missed";
  const progress = clamp((setup.bars_closed / setup.bars_needed) * 100);
  const where =
    price == null ? "" : price > hi ? "above the range" : price < lo ? "below the range" : "inside the range";

  return (
    <div
      role="img"
      aria-label={`${setup.symbol} opening range ${px(lo)} to ${px(hi)}, price ${px(price)} ${where}`}
      className={cn("relative pt-5", dead && "opacity-50")}
    >
      <div
        className="relative h-9 origin-left overflow-hidden rounded-md bg-bgSecondary animate-drawIn"
        style={{ animationDelay: `${delay}ms` }}
      >
        <div className="absolute inset-y-0 left-0 w-1/3 bg-loss/10" />
        <div className="absolute inset-y-0 right-0 w-1/3 bg-profit/10" />
        <div
          className="absolute inset-y-0 left-1/3 w-1/3 border-x"
          style={{ borderColor: ACCENT, backgroundColor: `${ACCENT}1F` }}
        >
          {forming && (
            <div
              className="h-full transition-[width] duration-1000 ease-out"
              style={{ width: `${progress}%`, backgroundColor: `${ACCENT}40` }}
            />
          )}
        </div>
        <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[11px] text-loss/80">short below</span>
        <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-profit/80">long above</span>
      </div>
      {pos != null && (
        <div
          className="pointer-events-none absolute bottom-0 top-0 transition-[left] duration-1000 ease-out"
          style={{ left: `${pos}%` }}
        >
          <span className="absolute left-1/2 top-0 -translate-x-1/2 whitespace-nowrap font-data text-[11px] text-textPrimary">
            {px(price)}
          </span>
          <span className="absolute bottom-0 left-0 top-5 w-[2px] -translate-x-1/2 rounded bg-textPrimary" />
        </div>
      )}
    </div>
  );
}

const STATUS_BADGE: Record<DayTradeSetup["status"], "new" | "neutral" | "warning"> = {
  forming: "new",
  waiting: "neutral",
  triggered: "neutral",
  missed: "warning",
  expired: "neutral",
};

function setupLine(s: DayTradeSetup, now: number): string {
  const { range_high: hi, range_low: lo, last_price: p } = s;
  switch (s.status) {
    case "forming":
      return `Range forming, ${s.bars_closed} of ${s.bars_needed} bars closed. Completes in ${countdown(
        new Date(s.range_end).getTime() - now,
      )}.`;
    case "waiting": {
      if (hi == null || lo == null || !p) return "Waiting for a closed bar beyond the range.";
      const up = ((hi - p) / p) * 100;
      const down = ((p - lo) / p) * 100;
      const rejected = s.skipped.length
        ? ` ${s.skipped.length} breakout${s.skipped.length > 1 ? "s" : ""} rejected, last one: ${
            s.skipped[s.skipped.length - 1].why
          }.`
        : "";
      return `${Math.max(up, 0).toFixed(2)}% to a long, ${Math.max(down, 0).toFixed(2)}% to a short.${rejected}`;
    }
    case "triggered": {
      const side = s.side === "LONG" ? "Long" : "Short";
      const fate =
        s.plan_status === "blocked"
          ? "The risk gate blocked the plan."
          : s.plan_status === "open"
            ? "Plan is open."
            : s.plan_status
              ? "Plan is closed."
              : "";
      return `${side} triggered at ${px(s.entry)} on ${s.vol_x ?? "?"}× volume. ${fate}`;
    }
    case "missed":
      return "The breakout happened before the scanner saw it, so no plan was written.";
    default:
      return "No valid breakout this session.";
  }
}

function SetupRow({ setup, now, index }: { setup: DayTradeSetup; now: number; index: number }) {
  const sideVariant =
    setup.status === "triggered" ? (setup.side === "LONG" ? "bullish" : "bearish") : STATUS_BADGE[setup.status];
  return (
    <li className="grid grid-cols-1 gap-x-4 gap-y-2 py-3 md:grid-cols-[7rem_1fr] md:items-center">
      <div className="flex items-center justify-between gap-2 md:block">
        <div className="font-data text-md font-semibold">{setup.symbol.replace("USDT", "")}</div>
        <Badge variant={sideVariant} className="mt-0 md:mt-1">
          {setup.status === "triggered" ? setup.side?.toLowerCase() : setup.status}
        </Badge>
      </div>
      <div>
        <RangeLadder setup={setup} delay={index * 70} />
        <div className="mt-1 flex justify-between font-data text-[11px] text-textMuted">
          <span className="w-1/3 text-right">{px(setup.range_low)}</span>
          <span className="w-1/3 text-right">{px(setup.range_high)}</span>
          <span className="w-1/3" />
        </div>
        <p className="mt-1 text-sm text-textSecondary">{setupLine(setup, now)}</p>
      </div>
    </li>
  );
}

// ---------- an open plan ----------

/** Stop on the left, target on the right, whatever the side: the axis is R, not price. */
function RRuler({ plan, price, targetR }: { plan: DayTradePlan; price: number | null; targetR: number }) {
  const s = plan.side === "LONG" ? 1 : -1;
  const risk = s * (plan.entry - plan.stop);
  const r = price == null || risk <= 0 ? null : (s * (price - plan.entry)) / risk;
  const lo = -1.25;
  const span = targetR + 0.25 - lo;
  const at = (v: number) => clamp(((v - lo) / span) * 100);
  const marks = [
    { v: -1, label: "Stop", value: plan.stop, tone: "text-loss" },
    { v: 0, label: "Entry", value: plan.entry, tone: "text-textPrimary" },
    { v: targetR, label: "Target", value: plan.target, tone: "text-profit" },
  ];
  return (
    <div
      role="img"
      aria-label={`Stop ${px(plan.stop)}, entry ${px(plan.entry)}, target ${px(plan.target)}, currently ${signedR(r)}`}
    >
      <div className="relative h-3 origin-left rounded-full bg-bgSecondary animate-drawIn">
        {r != null && (
          <div
            className={cn(
              "absolute inset-y-0 rounded-full transition-all duration-1000 ease-out",
              r >= 0 ? "bg-profit/70" : "bg-loss/70",
            )}
            style={{ left: `${Math.min(at(0), at(r))}%`, width: `${Math.abs(at(r) - at(0))}%` }}
          />
        )}
        {marks.map((m) => (
          <span key={m.label} className="absolute inset-y-0 w-[2px] bg-borderStrong" style={{ left: `${at(m.v)}%` }} />
        ))}
        {r != null && (
          <span
            className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bgCard bg-textPrimary transition-[left] duration-1000 ease-out"
            style={{ left: `${at(r)}%` }}
          >
            <span className="absolute inset-0 animate-pulseDot rounded-full bg-textPrimary/40" />
          </span>
        )}
      </div>
      <div className="relative mt-2 h-9">
        {marks.map((m) => (
          <div key={m.label} className="absolute -translate-x-1/2 text-center" style={{ left: `${at(m.v)}%` }}>
            <div className="text-[11px] text-textMuted">{m.label}</div>
            <div className={cn("font-data text-sm font-semibold", m.tone)}>{px(m.value)}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-sm text-textSecondary">
        {r == null ? "Waiting for a price." : `Now ${px(price)}, which is ${signedR(r)} before fees.`}
      </p>
    </div>
  );
}

function PlanCard({
  plan,
  price,
  desk,
  sizer,
  now,
}: {
  plan: DayTradePlan;
  price: number | null;
  desk: DayTradeDesk;
  sizer: Sizer;
  now: number;
}) {
  const pb = desk.playbook;
  const size = sizePosition(plan, sizer, pb);
  const base = plan.symbol.replace("USDT", "");
  const session = plan.session_at.slice(11, 16) === "00:00" ? "Daily open" : "US open";
  return (
    <Card className="animate-slideDown" variant="alert" accentColor={ACCENT}>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-data text-lg font-bold">{base}</span>
          <Badge variant={plan.side === "LONG" ? "bullish" : "bearish"}>{plan.side.toLowerCase()}</Badge>
          <span className="text-sm text-textSecondary">
            {session} breakout, flat by {plan.exit_by.slice(11, 16)} UTC (
            {countdown(new Date(plan.exit_by).getTime() - now)})
          </span>
        </div>
        <a
          href={tradeUrl(plan.exchange, plan.symbol)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-borderDefault px-2 py-1 text-sm text-textSecondary hover:bg-bgHover hover:text-textPrimary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400"
        >
          <ExternalLink size={14} />
          Open on {plan.exchange === "binance" ? "Binance" : "Bybit"}
        </a>
      </CardHeader>
      <CardBody className="space-y-4">
        <RRuler plan={plan} price={price} targetR={pb.target_r} />

        {plan.ai_verdict ? (
          <div className="flex items-start gap-2 rounded-md bg-bgSecondary p-3">
            <Badge variant={plan.ai_verdict === "take" ? "bullish" : "warning"} className="shrink-0 whitespace-nowrap">
              AI: {plan.ai_verdict}
            </Badge>
            <p className="text-sm text-textSecondary">{plan.ai_reason}</p>
          </div>
        ) : (
          <p className="text-sm text-textMuted">
            {desk.ai_enabled
              ? "No AI review came back for this plan."
              : "AI review is off. Add ANTHROPIC_API_KEY to get a take-or-skip opinion on each plan."}
          </p>
        )}

        <p className="text-sm text-textSecondary">
          Risking <span className="font-data text-textPrimary">${size.riskUsd.toFixed(2)}</span> at the stop, fees
          included: {plan.side === "LONG" ? "buy" : "sell"}{" "}
          <span className="font-data text-textPrimary">
            {size.qty.toLocaleString("en-US", { maximumSignificantDigits: 4 })} {base}
          </span>
          , about <span className="font-data text-textPrimary">${size.notional.toFixed(0)}</span> of position at{" "}
          <span className="font-data text-textPrimary">{size.leverage.toFixed(1)}×</span>.
          {size.capped && ` Capped by the ${pb.max_leverage}× leverage limit, so the real risk is smaller.`}
        </p>
      </CardBody>
    </Card>
  );
}

// ---------- risk budget + forward test ----------

function RiskBudget({ desk }: { desk: DayTradeDesk }) {
  const { gate, playbook: pb } = desk;
  const used = clamp(((gate.loss_r + gate.open) / pb.daily_loss_limit_r) * 100);
  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">Today's risk budget</h2>
        <span className="text-xs text-textMuted">resets 00:00 UTC</span>
      </CardHeader>
      <CardBody className="space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-textSecondary">Trades taken</span>
          <span className="flex items-center gap-1.5" aria-label={`${gate.trades} of ${pb.max_trades_per_day}`}>
            {Array.from({ length: pb.max_trades_per_day }, (_, i) => (
              <span
                key={i}
                className={cn("h-2.5 w-2.5 rounded-full", i < gate.trades ? "bg-textPrimary" : "bg-bgElevated")}
              />
            ))}
          </span>
        </div>
        <div>
          <div className="mb-1 flex justify-between text-sm">
            <span className="text-textSecondary">Losses plus open risk</span>
            <span className="font-data">
              {(gate.loss_r + gate.open).toFixed(1)} of {pb.daily_loss_limit_r}R
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-bgSecondary">
            <div
              className={cn("h-full rounded-full transition-[width] duration-700", used >= 100 ? "bg-loss" : "bg-warning")}
              style={{ width: `${used}%` }}
            />
          </div>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-textSecondary">Losing streak</span>
          <span className="font-data">
            {gate.streak} of {pb.max_consecutive_losses}
          </span>
        </div>
        {gate.blocked.length > 0 && (
          <p className="rounded-md bg-loss-subtle p-2 text-sm text-loss">
            New plans are blocked: {gate.blocked.join("; ")}.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function ForwardTest({ desk }: { desk: DayTradeDesk }) {
  const { stats } = desk;
  // Per-trade results recovered from the cumulative curve, to colour the tally.
  const results = stats.curve.map((c, i) => c - (i ? stats.curve[i - 1] : 0));
  const verdictText = {
    collecting: `Not proven, and the evidence so far is against it: replayed on the last 90 days, these rules lost about 0.2R per trade. Do not trade this with money. ${stats.n} of ${stats.gate_n} paper plans have closed. It is judged once, at ${stats.gate_n}, and a positive average is not enough: it must be clear of what luck alone produces.`,
    passed: `Passed its gate: the first ${stats.gate_n} paper plans averaged above the ${signedR(stats.bar)} bar after fees. That is evidence, not a guarantee.`,
    failed: `Failed its gate: the first ${stats.gate_n} paper plans did not clear the ${signedR(stats.bar)} bar after fees. Do not trade this playbook; later results do not reopen the question.`,
  }[stats.verdict];

  const curve = useMemo(() => {
    if (stats.curve.length < 2) return null;
    const min = Math.min(0, ...stats.curve);
    const max = Math.max(0, ...stats.curve);
    const y = (v: number) => 30 - ((v - min) / (max - min || 1)) * 28;
    const pts = stats.curve.map((v, i) => `${(i / (stats.curve.length - 1)) * 100},${y(v).toFixed(2)}`);
    return { d: `M${pts.join(" L")}`, zero: y(0) };
  }, [stats.curve]);

  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">Forward test</h2>
        <Badge variant={stats.verdict === "passed" ? "bullish" : stats.verdict === "failed" ? "bearish" : "warning"}>
          {stats.verdict === "collecting" ? "unproven" : stats.verdict}
        </Badge>
      </CardHeader>
      <CardBody className="space-y-3">
        <div
          className="grid gap-1"
          style={{ gridTemplateColumns: `repeat(${stats.gate_n / 3}, minmax(0, 1fr))` }}
          aria-label={`${Math.min(stats.n, stats.gate_n)} of ${stats.gate_n} plans closed`}
        >
          {Array.from({ length: stats.gate_n }, (_, i) => (
            <span
              key={i}
              title={results[i] != null ? signedR(results[i]) : "not traded yet"}
              className={cn(
                "h-3 rounded-sm",
                results[i] == null ? "bg-bgElevated" : results[i] > 0 ? "bg-profit" : "bg-loss",
              )}
            />
          ))}
        </div>
        <p className="text-sm text-textSecondary">{verdictText}</p>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <dl className="flex gap-5 text-sm">
            <div>
              <dt className="text-textMuted">Average</dt>
              <dd className="font-data text-md font-semibold">{signedR(stats.avg_r)}</dd>
            </div>
            <div>
              <dt className="text-textMuted">Won</dt>
              <dd className="font-data text-md font-semibold">
                {stats.wins} of {stats.n}
              </dd>
            </div>
            <div>
              <dt className="text-textMuted">Total</dt>
              <dd className="font-data text-md font-semibold">{signedR(stats.total_r)}</dd>
            </div>
          </dl>
          {curve && (
            <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-10 w-40" role="img" aria-label="Cumulative result in R">
              <line x1="0" x2="100" y1={curve.zero} y2={curve.zero} stroke="#334155" strokeWidth="0.5" />
              <path
                d={curve.d}
                pathLength={1}
                fill="none"
                stroke={stats.total_r >= 0 ? "#10B981" : "#EF4444"}
                strokeWidth="1.5"
                strokeDasharray="1"
                vectorEffect="non-scaling-stroke"
                className="animate-dash"
              />
            </svg>
          )}
        </div>
        {(stats.by_ai.take.n > 0 || stats.by_ai.skip.n > 0) && (
          <p className="text-sm text-textSecondary">
            Plans the AI would take: {stats.by_ai.take.n}, averaging {signedR(stats.by_ai.take.avg_r)}. Plans it would
            skip: {stats.by_ai.skip.n}, averaging {signedR(stats.by_ai.skip.avg_r)}. This is for comparison only and does
            not change the verdict.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function Playbook({ pb }: { pb: DayTradePlaybook }) {
  const rules = [
    `The first ${pb.range_minutes} minutes after each session open (${pb.sessions.map(sessionTime).join(" and ")}) set the range.`,
    "Enter after the first 5-minute bar that closes beyond the range. A wick through it does not count. The plan assumes you get the next bar's opening price, slightly worse.",
    `That bar needs at least ${pb.min_volume_x}× the average volume of the 20 bars before it, and must close on the trend's side of the 20-hour average.`,
    `The stop goes at the middle of the range. If it is so close that the target pays less than ${pb.min_net_rr}× the risk after fees, there is no plan.`,
    `The target is ${pb.target_r}× the risk. Whatever happens, the trade is closed ${pb.expire_hours} hours after the session open.`,
    `Each trade risks ${pb.risk_pct}% of the account, fees and slippage (${pb.friction_pct.toFixed(2)}%) included, at no more than ${pb.max_leverage}× leverage.`,
    `At most ${pb.max_trades_per_session} trades per session. Stop for the day after ${pb.max_trades_per_day} trades, ${pb.daily_loss_limit_r}R of losses, or ${pb.max_consecutive_losses} losses in a row.`,
  ];
  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">The playbook</h2>
        <span className="text-xs text-textMuted">fixed in code, not adjustable here</span>
      </CardHeader>
      <CardBody>
        <ul className="space-y-2 text-sm text-textSecondary">
          {rules.map((r) => (
            <li key={r} className="border-l-2 border-borderDefault pl-3">
              {r}
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

const RESULT_BADGE: Record<DayTradePlan["status"], "bullish" | "bearish" | "neutral" | "warning" | "new"> = {
  open: "new",
  target: "bullish",
  stop: "bearish",
  time: "neutral",
  blocked: "warning",
};

function Journal({ plans }: { plans: DayTradePlan[] }) {
  const rows = plans.filter((p) => p.status !== "open");
  return (
    <Card>
      <CardHeader>
        <h2 className="text-md font-semibold">Journal</h2>
        <span className="text-xs text-textMuted">paper results, after fees</span>
      </CardHeader>
      <CardBody className="p-0">
        {rows.length === 0 ? (
          <p className="p-4 text-sm text-textSecondary">
            Nothing has closed yet. Every plan lands here with its result once it hits the stop, the target or the
            time limit.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-textMuted">
                <tr>
                  <th className="px-4 py-2 font-medium">When (UTC)</th>
                  <th className="px-4 py-2 font-medium">Coin</th>
                  <th className="px-4 py-2 font-medium">Side</th>
                  <th className="px-4 py-2 font-medium">Outcome</th>
                  <th className="px-4 py-2 text-right font-medium">Result</th>
                  <th className="px-4 py-2 font-medium">AI</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className="border-t border-borderSubtle">
                    <td className="px-4 py-2 font-data text-textSecondary">
                      {p.triggered_at.slice(5, 16).replace("T", " ")}
                    </td>
                    <td className="px-4 py-2 font-data font-semibold">{p.symbol.replace("USDT", "")}</td>
                    <td className={cn("px-4 py-2", p.side === "LONG" ? "text-profit" : "text-loss")}>
                      {p.side.toLowerCase()}
                    </td>
                    <td className="px-4 py-2">
                      <Badge variant={RESULT_BADGE[p.status]}>{p.status}</Badge>
                      {p.status === "blocked" && (
                        <span className="ml-2 text-xs text-textMuted">{p.reasons[0]}</span>
                      )}
                    </td>
                    <td
                      className={cn(
                        "px-4 py-2 text-right font-data",
                        p.pnl_r == null ? "text-textMuted" : p.pnl_r > 0 ? "text-profit" : "text-loss",
                      )}
                    >
                      {signedR(p.pnl_r)}
                    </td>
                    <td className="px-4 py-2 text-textSecondary">{p.ai_verdict ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ---------- page ----------

export default function DayTradePage() {
  const now = useNow();
  const deskQ = useQuery({
    queryKey: ["daytrade", "desk"],
    queryFn: daytradeApi.desk,
    refetchInterval: 15_000,
  });
  const desk = deskQ.data;
  const [sizer, setSizer] = useSizer({ account: 1000, riskPct: 1 });
  const [session, setSession] = useState<string | null>(null);

  const setups = desk?.board?.setups ?? [];
  // Default tab: the session that opened most recently.
  const liveSession = useMemo(() => {
    let best: DayTradeSetup | null = null;
    for (const s of setups) if (!best || s.anchor > best.anchor) best = s;
    return best?.session ?? null;
  }, [setups]);
  const shown = session ?? liveSession;
  const priceBySymbol = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const s of setups) m.set(s.symbol, s.last_price);
    return m;
  }, [setups]);

  if (deskQ.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!desk) {
    return (
      <Card>
        <CardBody>
          <p className="text-sm text-textSecondary">
            The desk could not be loaded. Check that the API is running, then reload the page.
          </p>
        </CardBody>
      </Card>
    );
  }

  const openPlans = desk.plans.filter((p) => p.status === "open");
  const stale = desk.board ? now - new Date(desk.board.at).getTime() > 3 * 60_000 : true;
  const utc = new Date(now).toISOString().slice(11, 19);

  return (
    <div className="dt-desk space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">DayTrade desk</h1>
          <p className="max-w-[70ch] text-sm text-textSecondary">
            Range breakouts on {desk.symbols.length} liquid coins, using {desk.exchange === "binance" ? "Binance" : "Bybit"}{" "}
            prices. Each one comes with an entry, a stop, a target and a size. Nothing is sent to an exchange: these
            are paper plans, and you place the trade.
          </p>
        </div>
        <div className="flex items-center gap-4">
          <LiveIndicator live={desk.enabled && !stale} />
          <span className="font-data text-lg tabular-nums" aria-label="Current time, UTC">
            {utc} <span className="text-xs text-textMuted">UTC</span>
          </span>
        </div>
      </header>

      {!desk.enabled && (
        <Card variant="danger">
          <CardBody>
            <p className="text-sm text-textSecondary">
              The scanner is switched off. Set <code className="font-data">DAYTRADE_ENABLED=true</code> for the api and
              scheduler services, then restart them.
            </p>
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <RiskBudget desk={desk} />
        <ForwardTest desk={desk} />
      </div>

      <section className="space-y-3" aria-label="Open plans">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-md font-semibold">
            {openPlans.length ? `Open plans (${openPlans.length})` : "No open plans"}
          </h2>
          <div className="flex items-center gap-3 text-sm text-textSecondary">
            <label className="flex items-center gap-1.5">
              Account $
              <input
                type="number"
                min={1}
                value={sizer.account}
                onChange={(e) => setSizer({ ...sizer, account: Math.max(1, Number(e.target.value) || 0) })}
                className="w-24 rounded-md border border-borderDefault bg-bgSecondary px-2 py-1 font-data text-textPrimary focus:border-primary-500 focus:outline-none"
              />
            </label>
            <label className="flex items-center gap-1.5">
              Risk %
              <input
                type="number"
                min={0.1}
                max={5}
                step={0.1}
                value={sizer.riskPct}
                onChange={(e) =>
                  setSizer({ ...sizer, riskPct: Math.min(5, Math.max(0.1, Number(e.target.value) || 0)) })
                }
                className="w-16 rounded-md border border-borderDefault bg-bgSecondary px-2 py-1 font-data text-textPrimary focus:border-primary-500 focus:outline-none"
              />
            </label>
          </div>
        </div>
        {openPlans.length === 0 ? (
          <p className="text-sm text-textSecondary">
            A plan appears here the moment a setup below triggers and passes the risk budget. You also get it on
            Telegram.
          </p>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2 [&>*]:min-w-0">
            {openPlans.map((p) => (
              <PlanCard
                key={p.id}
                plan={p}
                price={priceBySymbol.get(p.symbol) ?? null}
                desk={desk}
                sizer={sizer}
                now={now}
              />
            ))}
          </div>
        )}
      </section>

      <Card>
        <CardHeader className="flex-wrap gap-2">
          <h2 className="text-md font-semibold">Setups</h2>
          <div className="flex gap-1" role="tablist" aria-label="Session">
            {desk.playbook.sessions.map((s) => (
              <button
                key={s}
                role="tab"
                aria-selected={shown === s}
                onClick={() => setSession(s)}
                className={cn(
                  "rounded-md px-3 py-1 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-400",
                  shown === s ? "bg-bgElevated text-textPrimary" : "text-textSecondary hover:bg-bgHover",
                )}
              >
                {SESSION_LABEL[s] ?? s} <span className="font-data text-xs text-textMuted">{sessionTime(s)}</span>
              </button>
            ))}
          </div>
        </CardHeader>
        <CardBody className="py-1">
          {setups.length === 0 ? (
            <p className="py-3 text-sm text-textSecondary">
              {desk.enabled
                ? "Waiting for the first scan. It runs every minute."
                : "No setups to show while the scanner is off."}
            </p>
          ) : (
            // Keyed by session so switching tabs replays the ladder draw-in.
            <ul key={shown} className="divide-y divide-borderSubtle">
              {setups
                .filter((s) => s.session === shown)
                .map((s, i) => (
                  <SetupRow key={s.symbol} setup={s} now={now} index={i} />
                ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr] [&>*]:min-w-0">
        <Playbook pb={desk.playbook} />
        <Journal plans={desk.plans} />
      </div>
    </div>
  );
}
