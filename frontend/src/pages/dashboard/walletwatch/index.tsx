import { useMemo, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { ExternalLink, Award, Wallet as WalletIcon } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Table, type Column } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { MetricCard } from "@/components/ui/MetricCard";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import {
  walletwatchApi,
  type DiscoveryRow,
  type DiscoveryWalletDetail,
} from "@/api/modules";

const CHAINS = ["", "ethereum", "bsc", "arbitrum", "base", "solana"] as const;
type ChainFilter = (typeof CHAINS)[number];

const EXPLORER: Record<string, string> = {
  ethereum: "https://etherscan.io/address/",
  bsc: "https://bscscan.com/address/",
  arbitrum: "https://arbiscan.io/address/",
  base: "https://basescan.org/address/",
  solana: "https://solscan.io/account/",
};

function shortAddr(a: string): string {
  if (!a) return "";
  return a.length <= 12 ? a : `${a.slice(0, 6)}…${a.slice(-4)}`;
}

export default function WalletWatchPage() {
  const [chain, setChain] = useState<ChainFilter>("");
  const [minRealized, setMinRealized] = useState<number>(0);
  const [minWinRate, setMinWinRate] = useState<number>(0);
  const [minTokens, setMinTokens] = useState<number>(1);
  const [onlyUnpromoted, setOnlyUnpromoted] = useState<boolean>(true);
  const [selected, setSelected] = useState<DiscoveryRow | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: [
      "walletwatch", "leaderboard",
      { chain, minRealized, minWinRate, minTokens, onlyUnpromoted },
    ],
    queryFn: () =>
      walletwatchApi.leaderboard({
        chain: chain || undefined,
        min_realized: minRealized || undefined,
        min_win_rate: minWinRate || undefined,
        min_token_count: minTokens > 1 ? minTokens : undefined,
        only_unpromoted: onlyUnpromoted,
        limit: 100,
      }),
    refetchInterval: 60_000,
  });

  const rows = data?.items ?? [];
  const stats = useMemo(() => {
    if (!rows.length) {
      return { count: 0, totalRealized: 0, topScore: 0, avgWinRate: 0 };
    }
    const totalRealized = rows.reduce((s, r) => s + r.total_realized_usd, 0);
    const avgWinRate =
      rows.reduce((s, r) => s + r.win_rate, 0) / rows.length;
    return {
      count: rows.length,
      totalRealized,
      topScore: rows[0]?.discovery_score ?? 0,
      avgWinRate,
    };
  }, [rows]);

  const lastUpdated = useMemo(() => {
    if (!rows.length) return null;
    const ts = rows.map((r) => new Date(r.last_scored_at).getTime());
    return new Date(Math.max(...ts));
  }, [rows]);

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">
            WalletWatch — PnL Discovery
          </h1>
          <p className="text-sm text-textSecondary">
            Smart-money candidates ranked by realized + unrealized PnL across alt buys.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <LiveIndicator />
          <LastUpdated date={lastUpdated} />
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Candidates" value={stats.count} valueDecimals={0} />
        <MetricCard
          label="Total Realized"
          value={stats.totalRealized}
          valueDecimals={0}
          valuePrefix="$"
        />
        <MetricCard
          label="Top Score"
          value={stats.topScore}
          valueDecimals={0}
        />
        <MetricCard
          label="Avg Win Rate"
          value={stats.avgWinRate * 100}
          valueDecimals={0}
          valueSuffix="%"
        />
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between w-full">
            <div className="grid grid-cols-2 gap-3 sm:flex sm:items-end">
              <div className="w-32">
                <Select
                  label="Chain"
                  value={chain}
                  onChange={(e) => setChain(e.target.value as ChainFilter)}
                >
                  <option value="">All chains</option>
                  {CHAINS.filter(Boolean).map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </Select>
              </div>
              <NumberFilter
                label="Min Realized ($)"
                value={minRealized}
                onChange={setMinRealized}
                step={10000}
              />
              <NumberFilter
                label="Min Win Rate"
                value={minWinRate}
                onChange={setMinWinRate}
                step={0.05}
                max={1}
                decimals={2}
              />
              <NumberFilter
                label="Min Tokens"
                value={minTokens}
                onChange={setMinTokens}
                step={1}
                min={1}
                decimals={0}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-textSecondary cursor-pointer select-none">
              <input
                type="checkbox"
                checked={onlyUnpromoted}
                onChange={(e) => setOnlyUnpromoted(e.target.checked)}
                className="h-4 w-4 rounded border-borderDefault bg-bgSecondary"
              />
              Hide already-promoted
            </label>
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {isLoading ? (
            <Skeleton className="m-4 h-64" />
          ) : (
            <LeaderboardTable rows={rows} onPick={setSelected} />
          )}
        </CardBody>
      </Card>

      <WalletDetailModal
        row={selected}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}

// ---------- table ----------

function LeaderboardTable({
  rows, onPick,
}: { rows: DiscoveryRow[]; onPick: (r: DiscoveryRow) => void }) {
  const cols: Column<DiscoveryRow>[] = [
    {
      key: "wallet",
      header: "Wallet",
      accessor: (r) => (
        <div className="flex items-center gap-2">
          <WalletIcon size={14} className="text-textMuted" />
          <span className="font-mono text-xs">{shortAddr(r.wallet_address)}</span>
          {r.promoted_at ? (
            <Badge variant="bullish" className="text-[10px]">PROMOTED</Badge>
          ) : null}
        </div>
      ),
    },
    {
      key: "chain",
      header: "Chain",
      accessor: (r) => <Badge variant="neutral">{r.chain}</Badge>,
    },
    {
      key: "realized",
      header: "Realized",
      align: "right",
      sortValue: (r) => r.total_realized_usd,
      accessor: (r) => (
        <span className={r.total_realized_usd >= 0 ? "text-bullish" : "text-loss"}>
          <NumberDisplay value={r.total_realized_usd} decimals={0} prefix="$" />
        </span>
      ),
    },
    {
      key: "unrealized",
      header: "Unrealized",
      align: "right",
      sortValue: (r) => r.total_unrealized_usd,
      accessor: (r) => (
        <span className={r.total_unrealized_usd >= 0 ? "text-bullish" : "text-loss"}>
          <NumberDisplay value={r.total_unrealized_usd} decimals={0} prefix="$" />
        </span>
      ),
    },
    {
      key: "win_rate",
      header: "Win Rate",
      align: "right",
      sortValue: (r) => r.win_rate,
      accessor: (r) => `${(r.win_rate * 100).toFixed(0)}% (${r.win_count}/${r.win_count + r.loss_count})`,
    },
    {
      key: "best",
      header: "Best ×",
      align: "right",
      sortValue: (r) => r.best_multiple,
      accessor: (r) => `${r.best_multiple.toFixed(2)}×`,
    },
    {
      key: "tokens",
      header: "Tokens",
      align: "right",
      sortValue: (r) => r.token_count,
      accessor: (r) => r.token_count,
    },
    {
      key: "score",
      header: "Score",
      align: "right",
      sortValue: (r) => r.discovery_score,
      accessor: (r) => (
        <span className="font-semibold text-primary-400">
          <NumberDisplay value={r.discovery_score} decimals={0} />
        </span>
      ),
    },
  ];
  return (
    <Table
      columns={cols}
      rows={rows}
      rowKey={(r) => r.wallet_address}
      onRowClick={onPick}
      emptyMessage="No discovery candidates yet — scoring runs hourly once DISCOVERY_ENABLED=true."
    />
  );
}

// ---------- detail + promote modal ----------

function WalletDetailModal({
  row, onClose,
}: { row: DiscoveryRow | null; onClose: () => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const detail = useQuery({
    queryKey: ["walletwatch", "wallet", row?.wallet_address],
    queryFn: () => walletwatchApi.walletDetail(row!.wallet_address),
    enabled: !!row,
  });

  const promote = useMutation({
    mutationFn: () => walletwatchApi.promote(row!.wallet_address, name.trim()),
    onSuccess: (res) => {
      if (!res.ok) {
        setError(res.reason || "Promotion failed");
        return;
      }
      qc.invalidateQueries({ queryKey: ["walletwatch", "leaderboard"] });
      onClose();
      setName("");
      setError(null);
    },
    onError: () => setError("Promotion request failed"),
  });

  if (!row) return null;
  const explorer = EXPLORER[row.chain];

  return (
    <Modal
      open={!!row}
      onClose={onClose}
      title="Wallet detail"
      className="max-w-2xl"
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm">{row.wallet_address}</span>
            {explorer ? (
              <a
                href={`${explorer}${row.wallet_address}`}
                target="_blank"
                rel="noreferrer"
                className="text-textMuted hover:text-textPrimary"
                aria-label="Open in block explorer"
              >
                <ExternalLink size={14} />
              </a>
            ) : null}
          </div>
          <Badge variant="neutral">{row.chain}</Badge>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="Realized" value={row.total_realized_usd} valueDecimals={0} valuePrefix="$" compact />
          <MetricCard label="Unrealized" value={row.total_unrealized_usd} valueDecimals={0} valuePrefix="$" compact />
          <MetricCard label="Win Rate" value={row.win_rate * 100} valueDecimals={0} valueSuffix="%" compact />
          <MetricCard label="Best ×" value={row.best_multiple} valueDecimals={2} valueSuffix="×" compact />
        </div>

        <div className="rounded-md border border-borderSubtle bg-bgSecondary">
          <div className="border-b border-borderSubtle px-3 py-2 text-xs font-semibold uppercase tracking-wide text-textMuted">
            Per-token PnL
          </div>
          {detail.isLoading ? (
            <Skeleton className="m-3 h-32" />
          ) : detail.data?.tokens?.length ? (
            <Table
              columns={tokenCols(row.chain)}
              rows={detail.data.tokens}
              rowKey={(t) => `${t.chain}:${t.token_address}`}
              dense
              emptyMessage="No per-token rows yet."
            />
          ) : (
            <p className="px-3 py-6 text-center text-sm text-textMuted">
              No per-token rows yet.
            </p>
          )}
        </div>

        {row.promoted_at ? (
          <div className="rounded-md border border-borderSubtle bg-bgSecondary px-3 py-2 text-sm text-textSecondary">
            <span className="font-semibold">Already promoted</span> on {new Date(row.promoted_at).toLocaleString()}.
          </div>
        ) : (
          <div className="flex flex-col gap-2 rounded-md border border-borderSubtle bg-bgSecondary px-3 py-3">
            <label className="text-xs font-semibold uppercase tracking-wide text-textMuted">
              Promote to whale_entities
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder='e.g. "PnL Discovery #1"'
                className="flex-1 rounded-md border border-borderSubtle bg-bgPrimary px-3 py-2 text-sm focus:border-primary-500 focus:outline-none"
              />
              <Button
                disabled={!name.trim() || promote.isPending}
                onClick={() => {
                  setError(null);
                  promote.mutate();
                }}
              >
                <Award size={14} className="mr-1" />
                Promote
              </Button>
            </div>
            {error ? <p className="text-xs text-loss">{error}</p> : null}
            <p className="text-xs text-textMuted">
              Adds the wallet to <code>whale_entity_addresses</code>; the Layer-1 detector
              will start emitting <code>smart_money_buy</code> alerts on its next swap.
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}

function tokenCols(walletChain: string): Column<DiscoveryWalletDetail["tokens"][number]>[] {
  return [
    {
      key: "symbol",
      header: "Token",
      accessor: (t) => (
        <div className="flex items-center gap-1">
          <span className="font-semibold">{t.token_symbol || shortAddr(t.token_address)}</span>
          {EXPLORER[walletChain] ? (
            <a
              href={`${EXPLORER[walletChain]}${t.token_address}`}
              target="_blank"
              rel="noreferrer"
              className="text-textMuted hover:text-textPrimary"
              onClick={(e) => e.stopPropagation()}
            >
              <ExternalLink size={11} />
            </a>
          ) : null}
        </div>
      ),
    },
    {
      key: "buy",
      header: "Bought",
      align: "right",
      accessor: (t) => <NumberDisplay value={t.total_buy_usd} decimals={0} prefix="$" />,
    },
    {
      key: "sell",
      header: "Sold",
      align: "right",
      accessor: (t) => <NumberDisplay value={t.total_sell_usd} decimals={0} prefix="$" />,
    },
    {
      key: "held",
      header: "Held",
      align: "right",
      accessor: (t) => <NumberDisplay value={t.current_value_usd} decimals={0} prefix="$" />,
    },
    {
      key: "realized",
      header: "Realized",
      align: "right",
      sortValue: (t) => t.realized_pnl_usd,
      accessor: (t) => (
        <span className={t.realized_pnl_usd >= 0 ? "text-bullish" : "text-loss"}>
          <NumberDisplay value={t.realized_pnl_usd} decimals={0} prefix="$" />
        </span>
      ),
    },
    {
      key: "mult",
      header: "×",
      align: "right",
      sortValue: (t) => t.multiple ?? 0,
      accessor: (t) => (t.multiple !== null ? `${t.multiple.toFixed(2)}×` : "—"),
    },
  ];
}

// ---------- small numeric input ----------

function NumberFilter({
  label, value, onChange, step = 1, min = 0, max, decimals = 0,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  decimals?: number;
}) {
  return (
    <div className="w-32">
      <label className="mb-1.5 block text-xs font-medium text-textSecondary">{label}</label>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          onChange(Number.isFinite(n) ? n : 0);
        }}
        step={step}
        min={min}
        max={max}
        className="w-full rounded-md border border-borderDefault bg-bgSecondary px-3 h-10 text-sm focus:border-borderStrong focus:outline-none"
      />
      {/* prevents tsc unused warning when decimals is wired through */}
      <span className="hidden">{decimals}</span>
    </div>
  );
}
