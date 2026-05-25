import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { MetricCard } from "@/components/ui/MetricCard";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import {
  listingApi,
  type ListingEvent,
  type ListingSignal,
  type ListingExchangeRef,
} from "@/api/modules";

const SIGNAL_LABEL: Record<string, string> = {
  pump_fade: "Pump Fade",
  breakout_long: "Breakout",
  initial_squeeze: "Squeeze",
  funding_extreme: "Funding",
  floor_held: "Floor Held",
  listing_detected: "Detected",
};

const SIGNAL_VARIANT: Record<
  string,
  "bullish" | "bearish" | "warning" | "neutral" | "new"
> = {
  pump_fade: "bearish",
  breakout_long: "bullish",
  initial_squeeze: "warning",
  funding_extreme: "warning",
  floor_held: "bullish",
  listing_detected: "new",
};

function timeAgo(iso: string): string {
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

function timeUntil(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "ended";
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function pricePctChange(t0: number | null, last: number | null): number | null {
  if (t0 == null || last == null || t0 === 0) return null;
  return (last / t0 - 1) * 100;
}

function ExchangeBadge({ exchange, market_type }: { exchange: string; market_type: string }) {
  return (
    <Badge variant="neutral" className="text-[10px] normal-case">
      {exchange}/{market_type}
    </Badge>
  );
}

export default function ListingWatchPage() {
  const [selected, setSelected] = useState<ListingEvent | null>(null);

  const activeQ = useQuery({
    queryKey: ["listings", "active"],
    queryFn: () => listingApi.active(),
    refetchInterval: 15_000,
  });

  const recentQ = useQuery({
    queryKey: ["listings", "recent", 7],
    queryFn: () => listingApi.recent(7),
    refetchInterval: 60_000,
  });

  const detailQ = useQuery({
    queryKey: ["listings", "detail", selected?.id],
    queryFn: () => listingApi.detail(selected!.id),
    enabled: !!selected,
  });

  const stats = useMemo(() => {
    const all = recentQ.data?.items ?? [];
    const active = activeQ.data?.items ?? [];
    return {
      active: active.length,
      cross: active.filter((e) => e.is_cross_listing).length,
      total7d: all.length,
      signals7d: all.reduce((sum, e) => sum + (e.signal_count ?? 0), 0),
    };
  }, [activeQ.data, recentQ.data]);

  const lastUpdated = useMemo(
    () => (activeQ.dataUpdatedAt ? new Date(activeQ.dataUpdatedAt) : null),
    [activeQ.dataUpdatedAt],
  );

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">
            ListingWatch — New listings + post-listing signals
          </h1>
          <p className="text-sm text-textSecondary">
            Detection across Bybit, Binance, OKX (spot + perp). 4-hour watcher fires structured signals on the
            inflection setups: pump-fade, breakout, squeeze, funding extremes, floor-held.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <LiveIndicator />
          <LastUpdated date={lastUpdated} label="Last refresh" />
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Active watchers" value={stats.active} valueDecimals={0} />
        <MetricCard label="Cross-listings (active)" value={stats.cross} valueDecimals={0} />
        <MetricCard label="Listings (7d)" value={stats.total7d} valueDecimals={0} />
        <MetricCard label="Signals (7d)" value={stats.signals7d} valueDecimals={0} />
      </div>

      <Card>
        <CardHeader>
          <div className="flex w-full items-center justify-between">
            <div>
              <h2 className="text-md font-semibold">Active watchers</h2>
              <p className="text-xs text-textSecondary">
                Listings detected within the last 4 hours, currently being scored
              </p>
            </div>
          </div>
        </CardHeader>
        <CardBody>
          {activeQ.isLoading ? (
            <Skeleton className="h-48" />
          ) : (activeQ.data?.items.length ?? 0) === 0 ? (
            <p className="py-6 text-center text-sm text-textMuted">
              No active listings right now. Detector polls every 60s — new symbols appear here within 1
              minute of going live on any tracked exchange.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {(activeQ.data?.items ?? []).map((event) => (
                <ActiveListingCard
                  key={event.id}
                  event={event}
                  onClick={() => setSelected(event)}
                />
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <h2 className="text-md font-semibold">Last 7 days</h2>
            <p className="text-xs text-textSecondary">All listings detected, ended or active</p>
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {recentQ.isLoading ? (
            <Skeleton className="m-4 h-64" />
          ) : (
            <Table<ListingEvent>
              columns={recentColumns}
              rows={recentQ.data?.items ?? []}
              rowKey={(r) => r.id}
              onRowClick={(r) => setSelected(r)}
              emptyMessage="No listings in the selected window."
            />
          )}
        </CardBody>
      </Card>

      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected
            ? `${selected.base_asset} — ${selected.exchange}/${selected.market_type}`
            : ""
        }
        className="max-w-2xl"
      >
        {selected && (
          <ListingDetail
            event={detailQ.data?.event ?? selected}
            signals={detailQ.data?.signals ?? []}
            loading={detailQ.isLoading}
          />
        )}
      </Modal>
    </div>
  );
}

function ActiveListingCard({
  event,
  onClick,
}: {
  event: ListingEvent;
  onClick: () => void;
}) {
  const change = pricePctChange(event.t0_price, event.last_price);
  const changeColor =
    change == null ? "text-textMuted" : change >= 0 ? "text-profit" : "text-loss";
  return (
    <div onClick={onClick} role="button" tabIndex={0} className="block">
      <Card
        variant="alert"
        accentColor="#EC4899"
        className="cursor-pointer hover:shadow-glow"
      >
        <CardBody className="space-y-2 p-4">
          <div className="flex items-start justify-between">
          <div>
            <div className="text-base font-semibold">{event.base_asset}</div>
            <div className="text-xs text-textMuted">{event.symbol}</div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <ExchangeBadge exchange={event.exchange} market_type={event.market_type} />
            {event.innovation && <Badge variant="warning">innovation</Badge>}
            {event.is_cross_listing && <Badge variant="new">cross-listing</Badge>}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <div className="text-xs text-textMuted">T-0 price</div>
            <NumberDisplay value={event.t0_price} decimals={6} />
          </div>
          <div>
            <div className="text-xs text-textMuted">Last</div>
            <NumberDisplay value={event.last_price} decimals={6} />
          </div>
          <div>
            <div className="text-xs text-textMuted">Change</div>
            <div className={changeColor}>
              {change == null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
            </div>
          </div>
          <div>
            <div className="text-xs text-textMuted">Signals</div>
            <div>{event.signal_count}</div>
          </div>
        </div>
        <div className="flex items-center justify-between text-xs text-textMuted">
          <span>Detected {timeAgo(event.detected_at)}</span>
          <span>Watcher ends in {timeUntil(event.watcher_ends_at)}</span>
        </div>
        {event.other_exchanges && event.other_exchanges.length > 0 && (
          <div className="flex flex-wrap gap-1 text-[10px] text-textMuted">
            {event.other_exchanges.map((s: ListingExchangeRef) => (
              <span key={`${s.exchange}-${s.market_type}-${s.symbol}`}>
                also on {s.exchange}/{s.market_type}
              </span>
            ))}
          </div>
        )}
        </CardBody>
      </Card>
    </div>
  );
}

function ListingDetail({
  event,
  signals,
  loading,
}: {
  event: ListingEvent;
  signals: ListingSignal[];
  loading: boolean;
}) {
  const change = pricePctChange(event.t0_price, event.last_price);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <div className="text-xs text-textMuted">Status</div>
          <Badge variant={event.status === "watching" ? "bullish" : "neutral"}>
            {event.status}
          </Badge>
        </div>
        <div>
          <div className="text-xs text-textMuted">T-0 price</div>
          <NumberDisplay value={event.t0_price} decimals={6} />
        </div>
        <div>
          <div className="text-xs text-textMuted">Last price</div>
          <NumberDisplay value={event.last_price} decimals={6} />
        </div>
        <div>
          <div className="text-xs text-textMuted">Change</div>
          <div
            className={
              change == null
                ? "text-textMuted"
                : change >= 0
                  ? "text-profit"
                  : "text-loss"
            }
          >
            {change == null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
          </div>
        </div>
        <div>
          <div className="text-xs text-textMuted">15m HH / LL</div>
          <div className="text-sm">
            <NumberDisplay value={event.high_15m} decimals={6} /> /{" "}
            <NumberDisplay value={event.low_15m} decimals={6} />
          </div>
        </div>
        <div>
          <div className="text-xs text-textMuted">1h HH / LL</div>
          <div className="text-sm">
            <NumberDisplay value={event.high_1h} decimals={6} /> /{" "}
            <NumberDisplay value={event.low_1h} decimals={6} />
          </div>
        </div>
        <div>
          <div className="text-xs text-textMuted">Funding</div>
          <NumberDisplay
            value={event.last_funding_pct == null ? null : event.last_funding_pct * 100}
            decimals={3}
            suffix="%"
          />
        </div>
        <div>
          <div className="text-xs text-textMuted">Watcher ends</div>
          <div>{timeUntil(event.watcher_ends_at)}</div>
        </div>
      </div>

      <div>
        <div className="mb-2 text-sm font-semibold">Signal history</div>
        {loading ? (
          <Skeleton className="h-20" />
        ) : signals.length === 0 ? (
          <p className="text-sm text-textMuted">No signals fired yet on this listing.</p>
        ) : (
          <div className="space-y-1.5">
            {signals.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between rounded border border-borderSubtle bg-bgElevated px-3 py-2 text-sm"
              >
                <div className="flex items-center gap-2">
                  <Badge variant={SIGNAL_VARIANT[s.signal_type] ?? "neutral"}>
                    {SIGNAL_LABEL[s.signal_type] ?? s.signal_type}
                  </Badge>
                  <span className="text-xs text-textMuted">{s.direction}</span>
                  <span className="text-xs text-textMuted">conv {s.conviction.toFixed(2)}</span>
                </div>
                <div className="text-xs text-textMuted">
                  T+
                  {s.seconds_since_t0 != null
                    ? Math.floor(s.seconds_since_t0 / 60)
                    : "?"}
                  m · {timeAgo(s.emitted_at)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const recentColumns: Column<ListingEvent>[] = [
  {
    key: "base_asset",
    header: "Token",
    accessor: (e) => (
      <div>
        <div className="font-medium">{e.base_asset}</div>
        <div className="text-xs text-textMuted">{e.symbol}</div>
      </div>
    ),
    sortValue: (e) => e.base_asset,
    sortable: true,
  },
  {
    key: "exchange",
    header: "Exchange",
    accessor: (e) => <ExchangeBadge exchange={e.exchange} market_type={e.market_type} />,
  },
  {
    key: "is_cross_listing",
    header: "Type",
    accessor: (e) => (
      <div className="flex flex-wrap gap-1">
        {e.innovation && <Badge variant="warning">innovation</Badge>}
        {e.is_cross_listing ? (
          <Badge variant="new">cross-listing</Badge>
        ) : (
          !e.innovation && <span className="text-textMuted">new</span>
        )}
      </div>
    ),
  },
  {
    key: "detected_at",
    header: "Detected",
    accessor: (e) => <span className="text-xs text-textMuted">{timeAgo(e.detected_at)}</span>,
    sortValue: (e) => new Date(e.detected_at).getTime(),
    sortable: true,
  },
  {
    key: "t0_price",
    header: "T-0",
    accessor: (e) => <NumberDisplay value={e.t0_price} decimals={6} />,
    align: "right",
  },
  {
    key: "last_price",
    header: "Last",
    accessor: (e) => {
      const change = pricePctChange(e.t0_price, e.last_price);
      return (
        <div className="text-right">
          <NumberDisplay value={e.last_price} decimals={6} />
          {change != null && (
            <div className={`text-xs ${change >= 0 ? "text-profit" : "text-loss"}`}>
              {change >= 0 ? "+" : ""}
              {change.toFixed(2)}%
            </div>
          )}
        </div>
      );
    },
    align: "right",
  },
  {
    key: "signal_count",
    header: "Signals",
    accessor: (e) => <span>{e.signal_count}</span>,
    sortValue: (e) => e.signal_count,
    sortable: true,
    align: "right",
  },
  {
    key: "status",
    header: "Status",
    accessor: (e) => (
      <Badge variant={e.status === "watching" ? "bullish" : "neutral"}>{e.status}</Badge>
    ),
  },
];
