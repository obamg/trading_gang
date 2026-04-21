import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  apiGetSettings,
  apiUpdateSettings,
  apiCreateTelegramToken,
  apiUnlinkTelegram,
  type UserSettings,
} from "@/api/settings";

function Toggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between py-3">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {description && <div className="text-xs text-textMuted mt-0.5">{description}</div>}
      </div>
      <button
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition-colors ${checked ? "bg-primary-500" : "bg-bgHover"}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform ${checked ? "translate-x-5" : ""}`}
        />
      </button>
    </div>
  );
}

function NumberInput({
  label,
  value,
  onChange,
  suffix,
  min,
  step,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  suffix?: string;
  min?: number;
  step?: number;
}) {
  return (
    <div className="flex items-center justify-between py-3">
      <span className="text-sm">{label}</span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={value}
          min={min}
          step={step}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-28 rounded-md border border-borderSubtle bg-bgSecondary px-2.5 py-1.5 text-right text-sm text-textPrimary focus:border-primary-500 focus:outline-none"
        />
        {suffix && <span className="text-xs text-textMuted">{suffix}</span>}
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: apiGetSettings,
  });

  const [draft, setDraft] = useState<Partial<UserSettings>>({});
  const [tgToken, setTgToken] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) setDraft(settings);
  }, [settings]);

  const saveMut = useMutation({
    mutationFn: apiUpdateSettings,
    onSuccess: (data) => {
      qc.setQueryData(["settings"], data);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const linkMut = useMutation({ mutationFn: apiCreateTelegramToken });
  const unlinkMut = useMutation({
    mutationFn: apiUnlinkTelegram,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  function patch(field: keyof UserSettings, value: unknown) {
    setDraft((prev) => ({ ...prev, [field]: value }));
  }

  function save() {
    saveMut.mutate(draft);
  }

  async function generateToken() {
    const token = await linkMut.mutateAsync();
    setTgToken(token);
  }

  if (isLoading) return <div className="p-6"><Skeleton className="h-64" /></div>;
  if (!settings) return null;

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Settings</h1>

      {/* Telegram */}
      <Card>
        <CardHeader>Telegram Notifications</CardHeader>
        <CardBody>
          {settings.telegram_linked ? (
            <>
              <div className="flex items-center justify-between py-2">
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 rounded-full bg-accent-green" />
                  <span className="text-sm">Telegram linked</span>
                </div>
                <button
                  onClick={() => unlinkMut.mutate()}
                  className="text-xs text-accent-red hover:underline"
                >
                  Unlink
                </button>
              </div>
              <Toggle
                label="Enable Telegram alerts"
                description="Receive real-time alerts via Telegram"
                checked={draft.telegram_enabled ?? false}
                onChange={(v) => patch("telegram_enabled", v)}
              />
            </>
          ) : (
            <div className="space-y-3 py-2">
              <p className="text-sm text-textSecondary">
                Connect your Telegram to receive alerts. Click below to generate a
                link token, then send <code className="text-xs bg-bgSecondary px-1.5 py-0.5 rounded">/link TOKEN</code> to
                the TradeCore bot.
              </p>
              {tgToken ? (
                <div className="rounded-md border border-borderSubtle bg-bgSecondary p-3">
                  <div className="text-xs text-textMuted mb-1">Your link token (expires in 10 min):</div>
                  <code className="text-sm font-mono text-primary-400 select-all break-all">{tgToken}</code>
                </div>
              ) : (
                <button
                  onClick={generateToken}
                  disabled={linkMut.isPending}
                  className="rounded-md bg-primary-500 px-4 py-2 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
                >
                  {linkMut.isPending ? "Generating..." : "Generate Link Token"}
                </button>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      {/* RadarX thresholds */}
      <Card>
        <CardHeader>RadarX Thresholds</CardHeader>
        <CardBody>
          <div className="divide-y divide-borderSubtle">
            <NumberInput label="Z-Score threshold" value={draft.radarx_zscore_threshold ?? 3} onChange={(v) => patch("radarx_zscore_threshold", v)} step={0.5} min={1} />
            <NumberInput label="Ratio threshold" value={draft.radarx_ratio_threshold ?? 4} onChange={(v) => patch("radarx_ratio_threshold", v)} step={0.5} min={1} suffix="x" />
            <NumberInput label="Min 24h volume" value={draft.radarx_min_volume_usd ?? 10_000_000} onChange={(v) => patch("radarx_min_volume_usd", v)} step={1_000_000} min={0} suffix="USD" />
            <NumberInput label="Cooldown" value={draft.radarx_cooldown_minutes ?? 30} onChange={(v) => patch("radarx_cooldown_minutes", v)} step={5} min={1} suffix="min" />
          </div>
        </CardBody>
      </Card>

      {/* WhaleRadar thresholds */}
      <Card>
        <CardHeader>WhaleRadar Thresholds</CardHeader>
        <CardBody>
          <div className="divide-y divide-borderSubtle">
            <NumberInput label="Min trade size" value={draft.whaleradar_min_trade_usd ?? 300_000} onChange={(v) => patch("whaleradar_min_trade_usd", v)} step={50_000} min={10_000} suffix="USD" />
            <NumberInput label="Min on-chain transfer" value={draft.whaleradar_min_onchain_usd ?? 500_000} onChange={(v) => patch("whaleradar_min_onchain_usd", v)} step={100_000} min={10_000} suffix="USD" />
          </div>
        </CardBody>
      </Card>

      {/* GemRadar thresholds */}
      <Card>
        <CardHeader>GemRadar Thresholds</CardHeader>
        <CardBody>
          <div className="divide-y divide-borderSubtle">
            <NumberInput label="Min market cap" value={draft.gemradar_min_mcap_usd ?? 1_000_000} onChange={(v) => patch("gemradar_min_mcap_usd", v)} step={500_000} min={0} suffix="USD" />
            <NumberInput label="Max market cap" value={draft.gemradar_max_mcap_usd ?? 100_000_000} onChange={(v) => patch("gemradar_max_mcap_usd", v)} step={10_000_000} min={0} suffix="USD" />
          </div>
        </CardBody>
      </Card>

      {/* Oracle */}
      <Card>
        <CardHeader>Oracle</CardHeader>
        <CardBody>
          <div className="divide-y divide-borderSubtle">
            <NumberInput label="Min score" value={draft.oracle_min_score ?? 65} onChange={(v) => patch("oracle_min_score", v)} step={5} min={0} />
            <NumberInput label="Min confluence" value={draft.oracle_min_confluence ?? 4} onChange={(v) => patch("oracle_min_confluence", v)} step={1} min={1} />
          </div>
        </CardBody>
      </Card>

      {/* Save */}
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saveMut.isPending}
          className="rounded-md bg-primary-500 px-6 py-2.5 text-sm font-semibold text-white hover:bg-primary-600 disabled:opacity-50"
        >
          {saveMut.isPending ? "Saving..." : "Save Settings"}
        </button>
        {saved && <span className="text-sm text-accent-green">Saved</span>}
      </div>
    </div>
  );
}
