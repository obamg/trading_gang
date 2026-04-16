import { Link } from "react-router-dom";
import { Check, ArrowRight } from "lucide-react";
import { MODULES } from "@/components/layout/modules";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-bgPrimary text-textPrimary">
      <Header />
      <Hero />
      <ModulesShowcase />
      <HowItWorks />
      <Pricing />
      <SocialProof />
      <Footer />
    </div>
  );
}

function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-borderSubtle bg-bgPrimary/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link to="/" className="text-lg font-bold tracking-tight">
          TradeCore<span className="text-accent">.</span>
        </Link>
        <nav className="flex items-center gap-5 text-sm">
          <a href="#modules" className="text-textSecondary hover:text-textPrimary">Modules</a>
          <a href="#how" className="text-textSecondary hover:text-textPrimary">How it works</a>
          <a href="#pricing" className="text-textSecondary hover:text-textPrimary">Pricing</a>
          <Link to="/login" className="text-textSecondary hover:text-textPrimary">Log in</Link>
          <Link
            to="/register"
            className="rounded-md bg-accent px-4 py-1.5 text-sm font-semibold text-bgPrimary hover:opacity-90"
          >
            Start free
          </Link>
        </nav>
      </div>
    </header>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-borderSubtle">
      {/* Subtle grid */}
      <div
        aria-hidden
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage:
            "linear-gradient(to right, #ffffff 1px, transparent 1px), linear-gradient(to bottom, #ffffff 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />
      <div className="relative mx-auto max-w-5xl px-6 py-28 text-center">
        <span className="mb-4 inline-block rounded-full border border-borderSubtle bg-bgElevated px-3 py-1 text-xs text-textSecondary">
          Pro-grade crypto intelligence · Built for traders
        </span>
        <h1 className="mb-4 text-5xl font-bold leading-tight md:text-6xl">
          Trade with an <span className="text-accent">edge</span>.
        </h1>
        <p className="mx-auto mb-8 max-w-2xl text-lg text-textSecondary">
          Ten connected modules watch the market so you don't have to — volume spikes,
          whale flows, liquidation maps, macro context, and a meta-Oracle that scores
          every opportunity from -100 to +100.
        </p>
        <div className="flex items-center justify-center gap-3">
          <Link
            to="/register"
            className="inline-flex items-center gap-2 rounded-md bg-accent px-6 py-3 text-sm font-semibold text-bgPrimary hover:opacity-90"
          >
            Start for free <ArrowRight size={16} />
          </Link>
          <a
            href="#modules"
            className="rounded-md border border-borderStrong px-6 py-3 text-sm font-semibold text-textPrimary hover:bg-bgElevated"
          >
            See it live
          </a>
        </div>
      </div>
    </section>
  );
}

const MODULE_BLURBS: Record<string, string> = {
  radarx: "Volume spikes detected on 5-minute candles via live z-scores.",
  whaleradar: "Large trades, OI surges, and on-chain transfers in real time.",
  liquidmap: "Heatmap of where leverage is stacked and ready to unwind.",
  sentimentpulse: "Funding, long/short ratios, and crowd positioning at a glance.",
  macropulse: "DXY, VIX, yields, and ETF flows distilled into a macro score.",
  gemradar: "Early-stage tokens with liquidity and risk scoring.",
  riskcalc: "Size every trade correctly before you click — no more guessing.",
  tradelog: "Journal every trade, tag setups, review the tape with context.",
  performancecore: "Win rate, expectancy, drawdown — brutal honesty on demand.",
  oracle: "Six modules voting on every setup, scored and sorted for you.",
};

function ModulesShowcase() {
  return (
    <section id="modules" className="border-b border-borderSubtle">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <h2 className="mb-3 text-center text-3xl font-bold">All ten modules, one cockpit</h2>
        <p className="mx-auto mb-12 max-w-2xl text-center text-textSecondary">
          Each module is specialised, fast, and opinionated. They don't just stream data —
          they emit signals. Oracle stitches them together.
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {MODULES.map((m) => {
            const Icon = m.icon;
            return (
              <div
                key={m.key}
                className="rounded-lg border border-borderSubtle bg-bgElevated p-5 transition hover:border-borderStrong"
              >
                <div className="mb-3 flex items-center gap-3">
                  <div
                    className="flex h-9 w-9 items-center justify-center rounded-md"
                    style={{ backgroundColor: `${m.color}26`, color: m.color }}
                  >
                    <Icon size={18} />
                  </div>
                  <span className="font-semibold">{m.label}</span>
                </div>
                <p className="text-sm text-textSecondary">{MODULE_BLURBS[m.key] ?? ""}</p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    { n: 1, title: "Connect", body: "Link your exchange with read-only keys. We never trade for you." },
    { n: 2, title: "Monitor", body: "Real-time alerts across ten modules — desktop, Telegram, or WebSocket feed." },
    { n: 3, title: "Decide", body: "Oracle aggregates every module into a single score. Trade only when confluence agrees." },
  ];
  return (
    <section id="how" className="border-b border-borderSubtle bg-bgElevated/40">
      <div className="mx-auto max-w-5xl px-6 py-20">
        <h2 className="mb-12 text-center text-3xl font-bold">How it works</h2>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {steps.map((s) => (
            <div key={s.n} className="rounded-lg border border-borderSubtle bg-bgPrimary p-6">
              <div className="mb-3 inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent font-bold text-bgPrimary">
                {s.n}
              </div>
              <h3 className="mb-2 text-lg font-semibold">{s.title}</h3>
              <p className="text-sm text-textSecondary">{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const PLANS = [
  {
    key: "free",
    name: "Free",
    price: "$0",
    cadence: "forever",
    features: ["3 core modules", "20 alerts / day", "Basic backtesting", "Community support"],
    cta: "Start free",
    href: "/register",
  },
  {
    key: "pro",
    name: "Pro",
    price: "$29",
    cadence: "per month",
    features: ["All 10 modules", "Unlimited alerts", "Oracle signal history", "Priority data feeds", "Telegram + email alerts"],
    cta: "Get Pro",
    href: "/register?plan=pro",
    highlight: true,
  },
  {
    key: "elite",
    name: "Elite",
    price: "$79",
    cadence: "per month",
    features: ["Everything in Pro", "API access", "Advanced backtesting", "Multi-account support", "Dedicated onboarding"],
    cta: "Get Elite",
    href: "/register?plan=elite",
  },
];

function Pricing() {
  return (
    <section id="pricing" className="border-b border-borderSubtle">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <h2 className="mb-3 text-center text-3xl font-bold">Simple, honest pricing</h2>
        <p className="mx-auto mb-12 max-w-2xl text-center text-textSecondary">
          Cancel anytime. Every plan includes read-only exchange links — we never take custody of funds.
        </p>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {PLANS.map((p) => (
            <div
              key={p.key}
              className={`flex flex-col rounded-lg border p-6 ${
                p.highlight
                  ? "border-accent bg-bgElevated shadow-glow"
                  : "border-borderSubtle bg-bgElevated"
              }`}
            >
              <div className="mb-5">
                <div className="mb-1 text-sm font-semibold text-textSecondary">{p.name}</div>
                <div className="flex items-baseline gap-2">
                  <span className="text-4xl font-bold">{p.price}</span>
                  <span className="text-sm text-textMuted">{p.cadence}</span>
                </div>
              </div>
              <ul className="mb-6 flex flex-col gap-2 text-sm">
                {p.features.map((f) => (
                  <li key={f} className="flex items-center gap-2 text-textSecondary">
                    <Check size={14} className="text-accent" />
                    {f}
                  </li>
                ))}
              </ul>
              <Link
                to={p.href}
                className={`mt-auto rounded-md px-4 py-2.5 text-center text-sm font-semibold ${
                  p.highlight
                    ? "bg-accent text-bgPrimary hover:opacity-90"
                    : "border border-borderStrong text-textPrimary hover:bg-bgPrimary"
                }`}
              >
                {p.cta}
              </Link>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function SocialProof() {
  const quotes = [
    { who: "Maya · Prop desk", text: "Oracle's been dead-on for regime shifts. Cut my size on red-score days, doubled it on high-conf longs." },
    { who: "Ryo · Independent", text: "RadarX catches moves before Twitter even notices. LiquidMap alone paid for the year." },
    { who: "D. · Fund analyst", text: "Finally, a tool that respects how pros actually work. Not another charting app." },
  ];
  return (
    <section className="border-b border-borderSubtle bg-bgElevated/40">
      <div className="mx-auto max-w-5xl px-6 py-20 text-center">
        <h2 className="mb-3 text-3xl font-bold">Join traders already using TradeCore</h2>
        <p className="mb-12 text-textSecondary">From solo discretionary traders to prop desks.</p>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {quotes.map((q) => (
            <blockquote
              key={q.who}
              className="flex flex-col gap-3 rounded-lg border border-borderSubtle bg-bgPrimary p-5 text-left"
            >
              <p className="text-sm text-textPrimary">"{q.text}"</p>
              <footer className="text-xs text-textMuted">— {q.who}</footer>
            </blockquote>
          ))}
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="bg-bgPrimary">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 md:flex-row">
        <div className="text-sm text-textMuted">
          TradeCore<span className="text-accent">.</span> © {new Date().getFullYear()}
        </div>
        <nav className="flex items-center gap-5 text-sm text-textSecondary">
          <a href="/privacy">Privacy</a>
          <a href="/terms">Terms</a>
          <a href="https://twitter.com" target="_blank" rel="noreferrer">Twitter</a>
          <a href="https://discord.com" target="_blank" rel="noreferrer">Discord</a>
        </nav>
      </div>
    </footer>
  );
}
