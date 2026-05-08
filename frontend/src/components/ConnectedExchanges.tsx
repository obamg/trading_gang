import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import {
  apiCreateCredential,
  apiDeleteCredential,
  apiListCredentials,
  apiSupportedExchanges,
  apiSyncCredential,
  type ExchangeCredential,
  type SupportedExchange,
} from "@/api/exchanges";

function formatRelative(iso: string | null): string {
  if (!iso) return "never";
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

function CredentialRow({ cred }: { cred: ExchangeCredential }) {
  const qc = useQueryClient();
  const syncMut = useMutation({
    mutationFn: () => apiSyncCredential(cred.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exchange-credentials"] }),
  });
  const deleteMut = useMutation({
    mutationFn: () => apiDeleteCredential(cred.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exchange-credentials"] }),
  });

  const lastSyncErr = cred.last_sync_error;
  const status = lastSyncErr ? "error" : cred.last_synced_at ? "synced" : "pending";

  return (
    <div className="flex items-center justify-between gap-3 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize">{cred.exchange}</span>
          {cred.label && cred.label !== "default" && (
            <span className="text-xs text-textMuted">/ {cred.label}</span>
          )}
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              status === "error"
                ? "bg-accent-red"
                : status === "synced"
                ? "bg-accent-green"
                : "bg-textMuted"
            }`}
          />
        </div>
        <div className="text-xs text-textMuted mt-0.5">
          Last sync: {formatRelative(cred.last_synced_at)}
          {lastSyncErr && (
            <span className="ml-2 text-accent-red">— {lastSyncErr.slice(0, 80)}</span>
          )}
        </div>
        {syncMut.data?.ok && (
          <div className="text-xs text-accent-green mt-0.5">
            Synced — {syncMut.data.trades_inserted} new, {syncMut.data.trades_skipped} skipped
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={() => syncMut.mutate()}
          disabled={syncMut.isPending}
          className="rounded-md border border-borderSubtle px-3 py-1.5 text-xs hover:bg-bgHover disabled:opacity-50"
        >
          {syncMut.isPending ? "Syncing..." : "Sync now"}
        </button>
        <button
          onClick={() => {
            if (confirm(`Disconnect ${cred.exchange}? Trades already imported stay.`)) {
              deleteMut.mutate();
            }
          }}
          disabled={deleteMut.isPending}
          className="rounded-md px-2 py-1.5 text-xs text-accent-red hover:bg-bgHover disabled:opacity-50"
          aria-label="Remove credential"
        >
          Remove
        </button>
      </div>
    </div>
  );
}

function ConnectForm({ exchanges }: { exchanges: SupportedExchange[] }) {
  const qc = useQueryClient();
  const [exchange, setExchange] = useState(exchanges[0]?.name ?? "");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);

  const selected = exchanges.find((e) => e.name === exchange);
  const requiresPassphrase = selected?.requires_passphrase ?? false;

  const mut = useMutation({
    mutationFn: apiCreateCredential,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["exchange-credentials"] });
      setApiKey("");
      setApiSecret("");
      setPassphrase("");
      setLabel("");
      setError(null);
    },
    onError: (err: AxiosError<{ detail?: string; message?: string }>) => {
      const msg =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        err.message ||
        "Failed to connect exchange";
      setError(msg);
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mut.mutate({
      exchange,
      api_key: apiKey.trim(),
      api_secret: apiSecret.trim(),
      passphrase: requiresPassphrase ? passphrase.trim() : undefined,
      label: label.trim() || undefined,
    });
  }

  return (
    <form onSubmit={submit} className="space-y-3 pt-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-textMuted">Exchange</span>
          <select
            value={exchange}
            onChange={(e) => setExchange(e.target.value)}
            className="mt-1 w-full rounded-md border border-borderSubtle bg-bgSecondary px-2.5 py-1.5 text-sm"
          >
            {exchanges.map((e) => (
              <option key={e.name} value={e.name}>
                {e.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-textMuted">Label (optional)</span>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. main"
            className="mt-1 w-full rounded-md border border-borderSubtle bg-bgSecondary px-2.5 py-1.5 text-sm"
          />
        </label>
      </div>
      <label className="block">
        <span className="text-xs text-textMuted">API Key</span>
        <input
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className="mt-1 w-full rounded-md border border-borderSubtle bg-bgSecondary px-2.5 py-1.5 font-mono text-sm"
          required
        />
      </label>
      <label className="block">
        <span className="text-xs text-textMuted">API Secret</span>
        <input
          type="password"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className="mt-1 w-full rounded-md border border-borderSubtle bg-bgSecondary px-2.5 py-1.5 font-mono text-sm"
          required
        />
      </label>
      {requiresPassphrase && (
        <label className="block">
          <span className="text-xs text-textMuted">Passphrase</span>
          <input
            type="password"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            className="mt-1 w-full rounded-md border border-borderSubtle bg-bgSecondary px-2.5 py-1.5 font-mono text-sm"
            required
          />
        </label>
      )}
      <p className="text-xs text-textMuted">
        Use a <strong>read-only</strong> key. Withdrawal-enabled keys are refused. We
        recommend IP-restricting the key to TradeCore's server.
      </p>
      {error && <div className="text-xs text-accent-red">{error}</div>}
      <button
        type="submit"
        disabled={mut.isPending}
        className="rounded-md bg-primary-500 px-4 py-2 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
      >
        {mut.isPending ? "Validating..." : "Connect exchange"}
      </button>
    </form>
  );
}

export default function ConnectedExchanges() {
  const { data: exchanges = [] } = useQuery({
    queryKey: ["supported-exchanges"],
    queryFn: apiSupportedExchanges,
  });
  const { data: creds = [], isLoading } = useQuery({
    queryKey: ["exchange-credentials"],
    queryFn: apiListCredentials,
  });

  return (
    <Card>
      <CardHeader>Connected Exchanges</CardHeader>
      <CardBody>
        {isLoading ? (
          <div className="text-sm text-textMuted py-2">Loading...</div>
        ) : creds.length === 0 ? (
          <p className="text-sm text-textSecondary py-2">
            Connect your exchange API keys to import your trade history. Performance
            metrics, equity curve, and per-setup analytics will populate automatically
            once your trades sync.
          </p>
        ) : (
          <div className="divide-y divide-borderSubtle">
            {creds.map((c) => (
              <CredentialRow key={c.id} cred={c} />
            ))}
          </div>
        )}
        {exchanges.length > 0 && <ConnectForm exchanges={exchanges} />}
      </CardBody>
    </Card>
  );
}
