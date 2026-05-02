import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Tabs } from "@/components/ui/Tabs";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { PercentChange } from "@/components/ui/PercentChange";
import { Skeleton } from "@/components/ui/Skeleton";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import { gemApi, type GemAlert } from "@/api/modules";

type Risk = "all" | "low" | "medium" | "high";

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

export default function GemRadarPage() {
  const [risk, setRisk] = useState<Risk>("all");
  const { data, isLoading } = useQuery({
    queryKey: ["gem", "alerts", risk],
    queryFn: () => gemApi.alerts({ risk: risk === "all" ? undefined : risk, limit: 50 }),
    refetchInterval: 30000,
  });

  const lastUpdated = useMemo(() => {
    const items = data?.items ?? [];
    return items.length ? new Date(items[0].detected_at) : null;
  }, [data]);

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">GemRadar — Early-stage token discovery</h1>
          <p className="text-sm text-textSecondary">Fresh pools with signal, filtered by risk profile.</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <LiveIndicator />
          <LastUpdated date={lastUpdated} />
        </div>
      </header>

      <Card>
        <CardHeader>
          <Tabs
            tabs={[
              { key: "all", label: "All" },
              { key: "low", label: "Low risk" },
              { key: "medium", label: "Medium risk" },
              { key: "high", label: "High risk" },
            ]}
            active={risk}
            onChange={(k) => setRisk(k as Risk)}
          />
        </CardHeader>
        <CardBody className="flex flex-col gap-2">
          {isLoading ? (
            <div className="flex flex-col gap-2"><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
          ) : (data?.items ?? []).length === 0 ? (
            <p className="text-sm text-textSecondary">No gems detected for this filter.</p>
          ) : (
            (data?.items ?? []).map((g) => <GemCard key={g.id} g={g} />)
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function GemCard({ g }: { g: GemAlert }) {
  const riskVariant: "bullish" | "warning" | "bearish" | "neutral" =
    g.risk_label === "low" ? "bullish" :
    g.risk_label === "medium" ? "warning" :
    g.risk_label === "high" ? "bearish" : "neutral";

  return (
    <div className="rounded-md border border-borderSubtle bg-bgElevated p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold">{g.symbol}</span>
            {g.name && <span className="text-xs text-textMuted">{g.name}</span>}
            {g.chain && <Badge variant="neutral">{g.chain.toUpperCase()}</Badge>}
            {g.risk_label && <Badge variant={riskVariant}>{g.risk_label.toUpperCase()} RISK</Badge>}
          </div>
          {g.risk_flags && g.risk_flags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {g.risk_flags.map((f) => (
                <span key={f} className="rounded bg-bgSubtle px-1.5 py-0.5 text-xs text-textMuted">{f.replace(/_/g, " ")}</span>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1 text-right">
          <div className="font-semibold"><NumberDisplay value={g.price_usd ?? 0} decimals={6} prefix="$" /></div>
          <div className="text-xs text-textSecondary">MC {formatUsd(g.market_cap_usd ?? 0)}</div>
          <div className="text-xs text-textSecondary">Liq {formatUsd(g.liquidity_usd ?? 0)}</div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs sm:gap-4">
        <span>5m: <PercentChange value={g.price_change_5m} /></span>
        <span>1h: <PercentChange value={g.price_change_1h} /></span>
        <span>24h: <PercentChange value={g.price_change_24h} /></span>
        <span className="text-textMuted">Vol 24h: {formatUsd(g.volume_24h_usd ?? 0)}</span>
      </div>
    </div>
  );
}
