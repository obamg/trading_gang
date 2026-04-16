import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Badge } from "@/components/ui/Badge";
import { Tabs } from "@/components/ui/Tabs";
import { Table, type Column } from "@/components/ui/Table";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Skeleton } from "@/components/ui/Skeleton";
import { tradelogApi, type Trade, type TradeCreate } from "@/api/modules";

type Filter = "all" | "open" | "closed" | "paper";

export default function TradeLogPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [showNew, setShowNew] = useState(false);
  const [selected, setSelected] = useState<Trade | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["tradelog", "list", filter],
    queryFn: () => tradelogApi.list({
      status: filter === "open" ? "open" : filter === "closed" ? "closed" : undefined,
      is_paper: filter === "paper" ? true : undefined,
      limit: 100,
    }),
  });

  const trades = data?.items ?? [];
  const stats = computeStats(trades);

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">TradeLog — Journal &amp; manual logbook</h1>
          <p className="text-sm text-textSecondary">Every trade, setup, and note in one place.</p>
        </div>
        <Button onClick={() => setShowNew(true)}>+ New Trade</Button>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="Total trades" value={stats.total} />
        <StatCard label="Wins" value={stats.wins} color="text-profit" />
        <StatCard label="Losses" value={stats.losses} color="text-loss" />
        <StatCard label="Win rate" value={stats.total > 0 ? `${((stats.wins / stats.total) * 100).toFixed(1)}%` : "—"} />
        <StatCard label="Net P&amp;L" value={<NumberDisplay value={stats.netPnl} decimals={2} prefix="$" sign />} />
      </section>

      <Card>
        <CardHeader>
          <Tabs
            tabs={[
              { key: "all", label: "All" },
              { key: "open", label: "Open" },
              { key: "closed", label: "Closed" },
              { key: "paper", label: "Paper" },
            ]}
            active={filter}
            onChange={(k) => setFilter(k as Filter)}
          />
        </CardHeader>
        <CardBody className="p-0">
          {isLoading ? <Skeleton className="m-4 h-64" /> : (
            <TradesTable rows={trades} onRowClick={setSelected} />
          )}
        </CardBody>
      </Card>

      <NewTradeModal
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={() => { setShowNew(false); qc.invalidateQueries({ queryKey: ["tradelog", "list"] }); }}
      />
      <TradeDetailModal
        trade={selected}
        onClose={() => setSelected(null)}
        onUpdated={() => { setSelected(null); qc.invalidateQueries({ queryKey: ["tradelog", "list"] }); }}
      />
    </div>
  );
}

function computeStats(trades: Trade[]) {
  let wins = 0, losses = 0, netPnl = 0, total = 0;
  for (const t of trades) {
    if (t.status !== "closed" || t.net_pnl_usd == null) continue;
    total++;
    netPnl += t.net_pnl_usd;
    if (t.net_pnl_usd > 0) wins++;
    else if (t.net_pnl_usd < 0) losses++;
  }
  return { total, wins, losses, netPnl };
}

function StatCard({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="rounded-md border border-borderSubtle bg-bgElevated p-3">
      <div className="text-xs text-textSecondary">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${color ?? ""}`}>{value}</div>
    </div>
  );
}

function TradesTable({ rows, onRowClick }: { rows: Trade[]; onRowClick: (t: Trade) => void }) {
  const columns: Column<Trade>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => (
      <button onClick={() => onRowClick(r)} className="font-semibold hover:text-accent">{r.symbol}</button>
    ) },
    { key: "side", header: "Side", accessor: (r) => <Badge variant={r.side === "long" ? "bullish" : "bearish"}>{r.side.toUpperCase()}</Badge> },
    { key: "status", header: "Status", accessor: (r) => <Badge variant={r.status === "open" ? "warning" : "neutral"}>{r.status}{r.is_paper ? " · paper" : ""}</Badge> },
    { key: "entry", header: "Entry", accessor: (r) => <NumberDisplay value={r.entry_price} decimals={4} />, align: "right" },
    { key: "exit", header: "Exit", accessor: (r) => r.exit_price != null ? <NumberDisplay value={r.exit_price} decimals={4} /> : "—", align: "right" },
    { key: "size", header: "Size", accessor: (r) => <NumberDisplay value={r.size} decimals={4} />, align: "right" },
    { key: "pnl", header: "Net P&L", accessor: (r) => r.net_pnl_usd != null ? <NumberDisplay value={r.net_pnl_usd} decimals={2} prefix="$" sign colored /> : "—", align: "right", sortValue: (r) => r.net_pnl_usd ?? 0 },
    { key: "r", header: "R", accessor: (r) => r.r_multiple != null ? <NumberDisplay value={r.r_multiple} decimals={2} suffix="R" sign /> : "—", align: "right" },
    { key: "setup", header: "Setup", accessor: (r) => r.setup_name ?? "—" },
    { key: "time", header: "Entry at", accessor: (r) => new Date(r.entry_at).toLocaleString(), align: "right" },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.id} emptyMessage="No trades logged yet." />;
}

function NewTradeModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState<TradeCreate>({
    symbol: "", side: "long", entry_price: 0, size: 0, asset_type: "futures",
    leverage: 1, is_paper: false, setup_name: "", notes: "",
  });
  const mut = useMutation({
    mutationFn: tradelogApi.create,
    onSuccess: () => onCreated(),
  });

  const handleNum = (k: keyof TradeCreate) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((f) => ({ ...f, [k]: parseFloat(e.target.value) || 0 } as TradeCreate));
  };

  return (
    <Modal open={open} onClose={onClose} title="Log new trade" className="max-w-lg">
      <form
        className="grid grid-cols-2 gap-3"
        onSubmit={(e) => { e.preventDefault(); mut.mutate(form); }}
      >
        <Input label="Symbol" value={form.symbol} onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() }))} required />
        <div>
          <label className="mb-1.5 block text-xs font-medium text-textSecondary">Side</label>
          <select
            value={form.side}
            onChange={(e) => setForm((f) => ({ ...f, side: e.target.value }))}
            className="h-10 w-full rounded-md border border-borderDefault bg-bgSecondary px-3 text-sm text-textPrimary"
          >
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
        </div>
        <Input label="Entry price" type="number" step="any" value={form.entry_price || ""} onChange={handleNum("entry_price")} required />
        <Input label="Size (units)" type="number" step="any" value={form.size || ""} onChange={handleNum("size")} required />
        <Input label="Leverage" type="number" value={form.leverage ?? 1} onChange={handleNum("leverage")} />
        <Input label="Stop-loss" type="number" step="any" value={form.stop_loss_price ?? ""} onChange={handleNum("stop_loss_price")} />
        <Input label="Take-profit" type="number" step="any" value={form.take_profit_price ?? ""} onChange={handleNum("take_profit_price")} />
        <Input label="Setup" value={form.setup_name ?? ""} onChange={(e) => setForm((f) => ({ ...f, setup_name: e.target.value }))} />
        <div className="col-span-2">
          <label className="mb-1.5 block text-xs font-medium text-textSecondary">Notes</label>
          <textarea
            value={form.notes ?? ""}
            onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            className="min-h-[80px] w-full rounded-md border border-borderDefault bg-bgSecondary px-3 py-2 text-sm text-textPrimary"
          />
        </div>
        <label className="col-span-2 flex items-center gap-2 text-sm text-textSecondary">
          <input type="checkbox" checked={form.is_paper ?? false} onChange={(e) => setForm((f) => ({ ...f, is_paper: e.target.checked }))} />
          Paper trade
        </label>
        <div className="col-span-2 flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={mut.isPending}>{mut.isPending ? "Saving…" : "Create"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function TradeDetailModal({ trade, onClose, onUpdated }: { trade: Trade | null; onClose: () => void; onUpdated: () => void }) {
  const [exit, setExit] = useState("");
  const [fees, setFees] = useState("");
  const [notes, setNotes] = useState("");
  const mut = useMutation({
    mutationFn: (patch: Parameters<typeof tradelogApi.patch>[1]) => tradelogApi.patch(trade!.id, patch),
    onSuccess: () => onUpdated(),
  });

  if (!trade) return null;

  const close = () => {
    const patch: Parameters<typeof tradelogApi.patch>[1] = {};
    if (exit) patch.exit_price = parseFloat(exit);
    if (fees) patch.fees_usd = parseFloat(fees);
    if (notes) patch.notes = notes;
    mut.mutate(patch);
  };

  return (
    <Modal open={!!trade} onClose={onClose} title={`${trade.symbol} · ${trade.side.toUpperCase()}`} className="max-w-lg">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div><span className="text-textSecondary">Entry:</span> <NumberDisplay value={trade.entry_price} decimals={4} /></div>
          <div><span className="text-textSecondary">Size:</span> <NumberDisplay value={trade.size} decimals={4} /></div>
          <div><span className="text-textSecondary">Status:</span> {trade.status}</div>
          <div><span className="text-textSecondary">Net P&L:</span> {trade.net_pnl_usd != null ? <NumberDisplay value={trade.net_pnl_usd} decimals={2} prefix="$" sign colored /> : "—"}</div>
          {trade.exit_price != null && <div><span className="text-textSecondary">Exit:</span> <NumberDisplay value={trade.exit_price} decimals={4} /></div>}
          {trade.r_multiple != null && <div><span className="text-textSecondary">R:</span> <NumberDisplay value={trade.r_multiple} decimals={2} suffix="R" sign /></div>}
        </div>
        {trade.notes && <div className="rounded border border-borderSubtle bg-bgElevated p-2 text-sm text-textSecondary">{trade.notes}</div>}
        {trade.status === "open" && (
          <div className="flex flex-col gap-2 border-t border-borderSubtle pt-3">
            <div className="text-xs font-semibold">Close trade</div>
            <div className="grid grid-cols-2 gap-2">
              <Input label="Exit price" type="number" step="any" value={exit} onChange={(e) => setExit(e.target.value)} />
              <Input label="Fees ($)" type="number" step="any" value={fees} onChange={(e) => setFees(e.target.value)} />
            </div>
            <Input label="Closing notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex justify-end">
              <Button onClick={close} disabled={!exit || mut.isPending}>{mut.isPending ? "Saving…" : "Close trade"}</Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
