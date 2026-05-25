import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { MetricCard } from "@/components/ui/MetricCard";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import {
  wavewatchApi,
  type WaveAsset,
  type WaveState,
} from "@/api/modules";

const COMPONENT_LABEL: Record<string, string> = {
  vol_baseline_rising: "Volume rising",
  green_ratio: "Buy bias",
  range_compression: "Compression",
  higher_lows: "Higher lows",
  funding_warmup: "Funding",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function scoreVariant(score: number | null): "bullish" | "warning" | "neutral" {
  if (score == null) return "neutral";
  if (score >= 0.6) return "bullish";
  if (score >= 0.4) return "warning";
  return "neutral";
}

function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-textMuted">—</span>;
  return (
    <Badge variant={scoreVariant(score)} className="text-xs">
      {score.toFixed(2)}
    </Badge>
  );
}

export default function WaveWatchPage() {
  const [selected, setSelected] = useState<WaveAsset | null>(null);

  const universeQ = useQuery({
    queryKey: ["wavewatch", "universe"],
    queryFn: () => wavewatchApi.universe("active"),
    refetchInterval: 30_000,
  });

  const items = universeQ.data?.items ?? [];

  const sorted = useMemo(
    () =>
      [...items].sort((a, b) => {
        const sa = a.latest_score ?? -1;
        const sb = b.latest_score ?? -1;
        return sb - sa;
      }),
    [items],
  );

  const stats = useMemo(() => {
    const scored = items.filter((i) => i.latest_score != null);
    const primed = scored.filter((i) => (i.latest_score ?? 0) >= 0.6).length;
    const recentAlerts = items.filter((i) => {
      if (!i.last_alerted_at) return false;
      return Date.now() - new Date(i.last_alerted_at).getTime() < 6 * 3600_000;
    }).length;
    return {
      total: items.length,
      scored: scored.length,
      primed,
      recentAlerts,
    };
  }, [items]);

  const columns: Column<WaveAsset>[] = [
    {
      key: "base_asset",
      header: "Token",
      accessor: (a) => (
        <div className="flex flex-col">
          <span className="font-semibold">{a.base_asset}</span>
          <span className="text-[10px] text-textMuted">{a.symbol}</span>
        </div>
      ),
      sortValue: (a) => a.base_asset,
      sortable: true,
    },
    {
      key: "market_type",
      header: "Market",
      accessor: (a) => (
        <Badge variant="neutral" className="text-[10px] normal-case">
          {a.exchange}/{a.market_type}
        </Badge>
      ),
    },
    {
      key: "latest_score",
      header: "Score",
      accessor: (a) => <ScoreBadge score={a.latest_score} />,
      sortValue: (a) => a.latest_score ?? -1,
      sortable: true,
    },
    {
      key: "last_alerted_at",
      header: "Last alert",
      accessor: (a) =>
        a.last_alerted_at ? (
          <span className="text-xs">{timeAgo(a.last_alerted_at)}</span>
        ) : (
          <span className="text-xs text-textMuted">—</span>
        ),
      sortValue: (a) =>
        a.last_alerted_at ? new Date(a.last_alerted_at).getTime() : 0,
      sortable: true,
    },
    {
      key: "latest_score_at",
      header: "Scored",
      accessor: (a) => (
        <span className="text-xs text-textMuted">
          {timeAgo(a.latest_score_at)}
        </span>
      ),
      sortValue: (a) =>
        a.latest_score_at ? new Date(a.latest_score_at).getTime() : 0,
      sortable: true,
    },
  ];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex items-center justify-between">
          <div>
            <div className="text-lg font-semibold">WaveWatch</div>
            <div className="text-xs text-textMuted">
              Continuous surveillance of Bybit Innovation Zone perps — fires a
              "wave incoming" alert when accumulation + range break confirm.
            </div>
          </div>
          <div className="flex items-center gap-2">
            <LiveIndicator />
            <LastUpdated
              date={
                universeQ.dataUpdatedAt
                  ? new Date(universeQ.dataUpdatedAt)
                  : null
              }
              label="Last refresh"
            />
          </div>
        </CardHeader>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Universe" value={stats.total} valueDecimals={0} />
        <MetricCard label="Scored" value={stats.scored} valueDecimals={0} />
        <MetricCard label="Primed (≥0.6)" value={stats.primed} valueDecimals={0} />
        <MetricCard label="Alerts (6h)" value={stats.recentAlerts} valueDecimals={0} />
      </div>

      <Card>
        <CardHeader>
          <div className="text-sm font-semibold">
            Innovation universe by score
          </div>
        </CardHeader>
        <CardBody>
          {universeQ.isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : sorted.length === 0 ? (
            <div className="py-8 text-center text-textMuted">
              Universe is empty. The next refresh runs every 15 min.
            </div>
          ) : (
            <Table<WaveAsset>
              columns={columns}
              rows={sorted}
              rowKey={(a) => a.id}
              onRowClick={(a) => setSelected(a)}
              emptyMessage="Universe is empty."
            />
          )}
        </CardBody>
      </Card>

      <Modal
        open={selected != null}
        onClose={() => setSelected(null)}
        title={
          selected
            ? `${selected.base_asset} — ${selected.exchange}/${selected.market_type}`
            : ""
        }
      >
        {selected && <WaveDetail symbol={selected.symbol} />}
      </Modal>
    </div>
  );
}

function WaveDetail({ symbol }: { symbol: string }) {
  const stateQ = useQuery({
    queryKey: ["wavewatch", "state", symbol],
    queryFn: () => wavewatchApi.state(symbol),
    refetchInterval: 15_000,
  });

  if (stateQ.isLoading || !stateQ.data) {
    return <Skeleton className="h-48 w-full" />;
  }

  const s: WaveState = stateQ.data;
  const live = s.live_score;
  const dwellMin = Math.floor(s.dwell_seconds / 60);

  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-textMuted">Live score</div>
          <ScoreBadge score={live?.score ?? null} />
        </div>
        <div>
          <div className="text-xs text-textMuted">Onset?</div>
          {live?.onset ? (
            <Badge variant="bullish">YES</Badge>
          ) : (
            <span className="text-textMuted">—</span>
          )}
        </div>
        <div>
          <div className="text-xs text-textMuted">Dwell above 0.6</div>
          <div>{dwellMin > 0 ? `${dwellMin}m` : "—"}</div>
        </div>
        <div>
          <div className="text-xs text-textMuted">Vol burst</div>
          <div>
            {live ? `${live.vol_ratio_now.toFixed(1)}× baseline` : "—"}
          </div>
        </div>
        <div>
          <div className="text-xs text-textMuted">Funding</div>
          <div>
            {s.funding_pct == null
              ? "—"
              : `${(s.funding_pct * 100).toFixed(3)}%`}
          </div>
        </div>
        <div>
          <div className="text-xs text-textMuted">Last alert</div>
          <div>{timeAgo(s.last_alert)}</div>
        </div>
      </div>

      {live && (
        <div>
          <div className="mb-1 text-xs text-textMuted">Score components</div>
          <div className="space-y-1">
            {Object.entries(live.components).map(([k, v]) => (
              <div
                key={k}
                className="flex items-center justify-between rounded bg-bgElevated px-2 py-1 text-xs"
              >
                <span>{COMPONENT_LABEL[k] ?? k}</span>
                <span className="font-mono">{v.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="text-xs text-textMuted">
        Candles available: {s.candles_available} (need ≥24 for scoring)
      </div>
    </div>
  );
}
