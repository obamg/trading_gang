import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricCard } from "@/components/ui/MetricCard";
import { Table, type Column } from "@/components/ui/Table";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { LastUpdated } from "@/components/ui/LastUpdated";
import { sentimentApi, type FundingRow } from "@/api/modules";

export default function SentimentPage() {
  const { data: overview } = useQuery({ queryKey: ["sentiment", "overview"], queryFn: sentimentApi.overview });
  const { data: funding, isLoading: fL } = useQuery({ queryKey: ["sentiment", "funding"], queryFn: () => sentimentApi.funding(30) });
  const { data: ls, isLoading: lsL } = useQuery({ queryKey: ["sentiment", "ls"], queryFn: () => sentimentApi.longShort(30) });

  const lastUpdated = useMemo(() => {
    if (overview?.snapshot_at) return new Date(overview.snapshot_at);
    return null;
  }, [overview]);

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">SentimentPulse — Crowd positioning</h1>
          <p className="text-sm text-textSecondary">Funding, long/short ratio, and macro sentiment gauges.</p>
        </div>
        <LastUpdated date={lastUpdated} label="Last snapshot" />
      </header>

      <section className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3 md:gap-3">
        <MetricCard label="Fear &amp; Greed" value={overview?.fear_greed_index ?? null} valueDecimals={0} valueSuffix={overview?.fear_greed_label ? ` · ${overview.fear_greed_label}` : ""} />
        <MetricCard label="BTC Dominance" value={overview?.btc_dominance_pct ?? null} valueSuffix="%" />
        <MetricCard label="Total Market Cap" value={overview?.total_mcap_usd ?? null} valuePrefix="$" valueDecimals={0} />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><h2 className="text-sm font-semibold">Funding rates (top 30)</h2></CardHeader>
          <CardBody className="p-0">{fL ? <Skeleton className="m-4 h-64" /> : <FundingTable rows={funding?.items ?? []} />}</CardBody>
        </Card>
        <Card>
          <CardHeader><h2 className="text-sm font-semibold">Long/Short ratios</h2></CardHeader>
          <CardBody className="p-0">{lsL ? <Skeleton className="m-4 h-64" /> : <LSTable rows={ls?.items ?? []} />}</CardBody>
        </Card>
      </div>
    </div>
  );
}

function FundingTable({ rows }: { rows: FundingRow[] }) {
  const columns: Column<FundingRow>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <span className="font-semibold">{r.symbol}</span> },
    {
      key: "rate",
      header: "Funding",
      align: "right",
      sortValue: (r) => r.funding_rate ?? 0,
      accessor: (r) => {
        const v = r.funding_rate ?? 0;
        const pct = v * 100;
        return (
          <span className={
            v > 0.0003 ? "text-loss" :
            v < -0.0001 ? "text-profit" : "text-textPrimary"
          }>
            <NumberDisplay value={pct} decimals={4} suffix="%" sign />
          </span>
        );
      },
    },
    {
      key: "signal",
      header: "Signal",
      accessor: (r) => {
        const v = r.funding_rate ?? 0;
        if (v > 0.0003) return <Badge variant="bearish">Longs overpaying</Badge>;
        if (v < -0.0001) return <Badge variant="bullish">Short squeeze</Badge>;
        return <Badge variant="neutral">Neutral</Badge>;
      },
    },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.symbol} emptyMessage="No funding snapshots yet." />;
}

function LSTable({ rows }: { rows: FundingRow[] }) {
  const columns: Column<FundingRow>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => <span className="font-semibold">{r.symbol}</span> },
    {
      key: "bar",
      header: "Long vs Short",
      accessor: (r) => {
        const long = r.long_ratio ?? 50;
        const short = 100 - long;
        const extreme = long >= 65 || short >= 65;
        return (
          <div className="flex items-center gap-2">
            <div className="relative h-4 w-24 overflow-hidden rounded bg-bgElevated sm:w-40">
              <div className="absolute inset-y-0 left-0 bg-profit/60" style={{ width: `${long}%` }} />
              <div className="absolute inset-y-0 right-0 bg-loss/60" style={{ width: `${short}%` }} />
            </div>
            <span className={`text-xs tabular-nums ${extreme ? "text-warning font-semibold" : "text-textSecondary"}`}>
              {long.toFixed(0)}% / {short.toFixed(0)}%
            </span>
          </div>
        );
      },
    },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.symbol} emptyMessage="No long/short ratios yet." />;
}
