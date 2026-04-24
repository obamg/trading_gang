import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Tabs } from "@/components/ui/Tabs";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { PercentChange } from "@/components/ui/PercentChange";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  whaleApi, type WhaleTrade, type OnchainTransfer, type OISurge,
} from "@/api/modules";

type TabKey = "trades" | "oi" | "onchain";

export default function WhaleRadarPage() {
  const [tab, setTab] = useState<TabKey>("trades");
  const { data: trades, isLoading: tL } = useQuery({
    queryKey: ["whale", "trades"], queryFn: () => whaleApi.trades({ hours: 6 }),
  });
  const { data: oi, isLoading: oL } = useQuery({
    queryKey: ["whale", "oi"], queryFn: () => whaleApi.oiSurges({ hours: 12 }),
  });
  const { data: chain, isLoading: cL } = useQuery({
    queryKey: ["whale", "chain"], queryFn: () => whaleApi.onchain({ hours: 12 }),
  });

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">WhaleRadar — Smart-money tracking</h1>
          <p className="text-sm text-textSecondary">Large trades, OI surges, and on-chain transfers.</p>
        </div>
        <LiveIndicator />
      </header>

      <Card>
        <CardHeader>
          <Tabs
            tabs={[
              { key: "trades", label: `Large Trades (${trades?.items.length ?? 0})` },
              { key: "oi", label: `OI Surges (${oi?.items.length ?? 0})` },
              { key: "onchain", label: `On-Chain (${chain?.items.length ?? 0})` },
            ]}
            active={tab}
            onChange={(k) => setTab(k as TabKey)}
          />
        </CardHeader>
        <CardBody className="p-0">
          {tab === "trades" && (tL ? <Skeleton className="m-4 h-48" /> : <TradesTable rows={trades?.items ?? []} />)}
          {tab === "oi" && (oL ? <Skeleton className="m-4 h-48" /> : <OITable rows={oi?.items ?? []} />)}
          {tab === "onchain" && (cL ? <Skeleton className="m-4 h-48" /> : <ChainTable rows={chain?.items ?? []} />)}
        </CardBody>
      </Card>
    </div>
  );
}

function TradesTable({ rows }: { rows: WhaleTrade[] }) {
  const columns: Column<WhaleTrade>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <span className="font-semibold">{r.symbol}</span> },
    { key: "size", header: "Size (USD)", accessor: (r) => <span className="font-semibold"><NumberDisplay value={r.trade_size_usd} decimals={0} prefix="$" /></span>, align: "right", sortValue: (r) => r.trade_size_usd },
    { key: "side", header: "Side", accessor: (r) => <Badge variant={r.side.toLowerCase() === "buy" ? "bullish" : "bearish"}>{r.side.toUpperCase()}</Badge> },
    { key: "price", header: "Price", accessor: (r) => <NumberDisplay value={r.price} decimals={4} />, align: "right" },
    { key: "time", header: "Time", accessor: (r) => new Date(r.detected_at).toLocaleTimeString(), align: "right" },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.id} emptyMessage="No whale trades detected yet." />;
}

function OITable({ rows }: { rows: OISurge[] }) {
  const columns: Column<OISurge>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <span className="font-semibold">{r.symbol}</span> },
    { key: "before", header: "OI Before", accessor: (r) => <NumberDisplay value={r.oi_before_usd} decimals={0} prefix="$" />, align: "right" },
    { key: "after", header: "OI After", accessor: (r) => <NumberDisplay value={r.oi_after_usd} decimals={0} prefix="$" />, align: "right" },
    { key: "change", header: "Δ", accessor: (r) => <PercentChange value={r.oi_change_pct} />, align: "right", sortValue: (r) => r.oi_change_pct },
    { key: "price", header: "Price", accessor: (r) => <NumberDisplay value={r.price} decimals={4} />, align: "right" },
    { key: "dir", header: "Direction", accessor: (r) => r.direction ? <Badge variant={r.direction === "long_heavy" ? "bullish" : r.direction === "short_heavy" ? "bearish" : "warning"}>{r.direction.replace("_", " ")}</Badge> : null },
    { key: "time", header: "Time", accessor: (r) => new Date(r.detected_at).toLocaleTimeString(), align: "right" },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.id} emptyMessage="No OI surges in the last 12h." />;
}

function ChainTable({ rows }: { rows: OnchainTransfer[] }) {
  const columns: Column<OnchainTransfer>[] = [
    { key: "asset", header: "Asset", accessor: (r) => <span className="font-semibold">{r.asset}</span> },
    { key: "amount", header: "Amount", accessor: (r) => <NumberDisplay value={r.amount} decimals={4} />, align: "right" },
    { key: "usd", header: "USD Value", accessor: (r) => <NumberDisplay value={r.amount_usd} decimals={0} prefix="$" />, align: "right", sortValue: (r) => r.amount_usd },
    { key: "from", header: "From", accessor: (r) => <span className="text-textSecondary">{r.from_label ?? "—"}</span> },
    { key: "to", header: "To", accessor: (r) => <span className="text-textSecondary">{r.to_label ?? "—"}</span> },
    { key: "type", header: "Type", accessor: (r) => r.transfer_type ? (
      <Badge variant={r.transfer_type.includes("exchange_inflow") ? "warning" : r.transfer_type.includes("exchange_outflow") ? "bullish" : "neutral"}>
        {r.transfer_type.replace(/_/g, " ")}
      </Badge>
    ) : null },
    { key: "time", header: "Time", accessor: (r) => new Date(r.detected_at).toLocaleTimeString(), align: "right" },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.id} emptyMessage="No on-chain transfers detected." />;
}
