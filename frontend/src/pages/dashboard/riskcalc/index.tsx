import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { Table, type Column } from "@/components/ui/Table";
import { riskcalcApi, type CalcInput, type CalcResult, type CalcHistoryRow } from "@/api/modules";

export default function RiskCalcPage() {
  const [params] = useSearchParams();
  const [form, setForm] = useState<CalcInput>({
    account_balance_usd: 10000,
    risk_pct: 1,
    entry_price: 0,
    stop_loss_price: 0,
    take_profit_price: undefined,
    max_leverage: 10,
    asset_type: "futures",
    side: "long",
    symbol: "",
  });
  const [result, setResult] = useState<CalcResult | null>(null);

  useEffect(() => {
    const symbol = params.get("symbol");
    const entry = params.get("entry");
    if (symbol || entry) {
      setForm((f) => ({
        ...f,
        symbol: symbol ?? f.symbol,
        entry_price: entry ? parseFloat(entry) : f.entry_price,
      }));
    }
  }, [params]);

  const { data: history, refetch: refetchHistory } = useQuery({
    queryKey: ["riskcalc", "history"],
    queryFn: () => riskcalcApi.history(20),
  });

  const calc = useMutation({
    mutationFn: riskcalcApi.calculate,
    onSuccess: (r) => {
      setResult(r);
      refetchHistory();
    },
  });

  const handleNumber = (k: keyof CalcInput) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value === "" ? undefined : parseFloat(e.target.value);
    setForm((f) => ({ ...f, [k]: v as never }));
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    calc.mutate({
      ...form,
      take_profit_price: form.take_profit_price || undefined,
      symbol: form.symbol || undefined,
    });
  };

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header>
        <h1 className="text-lg font-semibold md:text-2xl">RiskCalc — Position sizing</h1>
        <p className="text-sm text-textSecondary">Compute size, leverage, liquidation, and R:R before you click.</p>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><h2 className="text-sm font-semibold">Parameters</h2></CardHeader>
          <CardBody>
            <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={submit}>
              <Input label="Symbol" value={form.symbol ?? ""} onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value }))} />
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
              <Input label="Account balance ($)" type="number" value={form.account_balance_usd} onChange={handleNumber("account_balance_usd")} />
              <Input label="Risk %" type="number" step="0.1" value={form.risk_pct} onChange={handleNumber("risk_pct")} />
              <Input label="Entry price" type="number" step="any" value={form.entry_price || ""} onChange={handleNumber("entry_price")} />
              <Input label="Stop-loss price" type="number" step="any" value={form.stop_loss_price || ""} onChange={handleNumber("stop_loss_price")} />
              <Input label="Take-profit (optional)" type="number" step="any" value={form.take_profit_price ?? ""} onChange={handleNumber("take_profit_price")} />
              <Input label="Max leverage" type="number" value={form.max_leverage ?? ""} onChange={handleNumber("max_leverage")} />
              <div className="sm:col-span-2">
                <Button type="submit" disabled={calc.isPending}>{calc.isPending ? "Calculating…" : "Calculate"}</Button>
              </div>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader><h2 className="text-sm font-semibold">Result</h2></CardHeader>
          <CardBody>
            {!result ? (
              <p className="text-sm text-textSecondary">Run a calculation to see the breakdown.</p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-3">
                  <Stat label="Position size" value={<NumberDisplay value={result.position_size_usd} decimals={2} prefix="$" />} />
                  <Stat label="Size (units)" value={<NumberDisplay value={result.position_size_units} decimals={6} />} />
                  <Stat label="Risk amount" value={<NumberDisplay value={result.risk_amount_usd} decimals={2} prefix="$" />} />
                  <Stat label="Stop distance" value={<NumberDisplay value={result.stop_distance_pct} decimals={2} suffix="%" />} />
                  <Stat label="Leverage" value={<NumberDisplay value={result.leverage} decimals={2} suffix="x" />} />
                  <Stat label="Liquidation" value={result.liquidation_price ? <NumberDisplay value={result.liquidation_price} decimals={4} /> : "—"} />
                </div>

                <PnlBreakdown maxLoss={result.max_loss_usd} potentialProfit={result.potential_profit_usd} rrRatio={result.rr_ratio} />

                {result.warnings.length > 0 && (
                  <div className="flex flex-col gap-1 rounded border border-warning/40 bg-warning/10 p-3">
                    <div className="text-xs font-semibold text-warning">Warnings</div>
                    {result.warnings.map((w) => (
                      <div key={w} className="flex items-center gap-2 text-xs text-textSecondary">
                        <Badge variant="warning">!</Badge>{w}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader><h2 className="text-sm font-semibold">Recent calculations</h2></CardHeader>
        <CardBody className="p-0">
          <HistoryTable rows={history?.items ?? []} />
        </CardBody>
      </Card>
    </div>
  );
}

function PnlBreakdown({ maxLoss, potentialProfit, rrRatio }: { maxLoss: number; potentialProfit: number | null; rrRatio: number | null }) {
  const hasTP = potentialProfit != null && potentialProfit > 0;
  const lossPct = hasTP ? maxLoss / (maxLoss + potentialProfit) * 100 : 50;
  const profitPct = hasTP ? 100 - lossPct : 0;

  return (
    <div className="rounded-md border border-borderSubtle bg-bgElevated p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-semibold text-textSecondary">PNL Breakdown</span>
        {rrRatio != null && (
          <span className={`text-sm font-bold ${rrRatio >= 2 ? "text-profit" : rrRatio >= 1 ? "text-warning" : "text-loss"}`}>
            {rrRatio.toFixed(2)} R:R
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="rounded border border-loss/40 bg-loss-subtle p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-loss/70">Max Loss</div>
          <div className="mt-0.5 text-lg font-bold text-loss">
            -$<NumberDisplay value={maxLoss} decimals={2} />
          </div>
        </div>
        <div className={`rounded border p-2.5 ${hasTP ? "border-profit/40 bg-profit-subtle" : "border-borderSubtle bg-bgCard"}`}>
          <div className={`text-[10px] uppercase tracking-wider ${hasTP ? "text-profit/70" : "text-textMuted"}`}>Potential Profit</div>
          <div className={`mt-0.5 text-lg font-bold ${hasTP ? "text-profit" : "text-textMuted"}`}>
            {hasTP ? <>+$<NumberDisplay value={potentialProfit} decimals={2} /></> : "Set TP"}
          </div>
        </div>
      </div>

      {hasTP && (
        <div>
          <div className="flex h-3 w-full overflow-hidden rounded-full">
            <div className="bg-loss/60 transition-all" style={{ width: `${lossPct}%` }} />
            <div className="bg-profit/60 transition-all" style={{ width: `${profitPct}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between text-[10px] text-textMuted">
            <span>Risk {lossPct.toFixed(0)}%</span>
            <span>Reward {profitPct.toFixed(0)}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border border-borderSubtle bg-bgElevated p-3">
      <div className="text-xs text-textSecondary">{label}</div>
      <div className="mt-1 font-semibold">{value}</div>
    </div>
  );
}

function HistoryTable({ rows }: { rows: CalcHistoryRow[] }) {
  const columns: Column<CalcHistoryRow>[] = [
    { key: "symbol", header: "Symbol", accessor: (r) => r.symbol ?? "—" },
    { key: "entry", header: "Entry", accessor: (r) => <NumberDisplay value={r.entry_price} decimals={4} />, align: "right" },
    { key: "stop", header: "Stop", accessor: (r) => <NumberDisplay value={r.stop_loss_price} decimals={4} />, align: "right" },
    { key: "size", header: "Size", accessor: (r) => <NumberDisplay value={r.position_size_usd} decimals={0} prefix="$" />, align: "right" },
    { key: "lev", header: "Lev", accessor: (r) => <NumberDisplay value={r.leverage} decimals={1} suffix="x" />, align: "right" },
    { key: "loss", header: "Max Loss", accessor: (r) => <span className="text-loss">-$<NumberDisplay value={r.max_loss_usd} decimals={0} /></span>, align: "right", sortValue: (r) => r.max_loss_usd },
    { key: "profit", header: "Profit", accessor: (r) => r.potential_profit_usd != null ? <span className="text-profit">+$<NumberDisplay value={r.potential_profit_usd} decimals={0} /></span> : "—", align: "right", sortValue: (r) => r.potential_profit_usd ?? 0 },
    { key: "rr", header: "R:R", accessor: (r) => r.rr_ratio != null ? <span className={r.rr_ratio >= 2 ? "text-profit" : r.rr_ratio >= 1 ? "text-warning" : "text-loss"}><NumberDisplay value={r.rr_ratio} decimals={2} /></span> : "—", align: "right" },
    { key: "time", header: "Time", accessor: (r) => new Date(r.calculated_at).toLocaleString(), align: "right" },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.id} emptyMessage="No calculations yet." />;
}
