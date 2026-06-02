import { LogOut, Menu } from "lucide-react";
import { NotificationCenter } from "./NotificationCenter";
import { useMacroStore } from "@/stores/macroStore";
import { useAuthStore } from "@/stores/authStore";
import { useSettingsStore } from "@/stores/settingsStore";
import { apiLogout } from "@/api/auth";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { useWebSocketStore } from "@/stores/webSocketStore";

function MacroMetric({ label, value, decimals = 2, suffix }: { label: string; value: number | null; decimals?: number; suffix?: string }) {
  return (
    <div className="flex items-center gap-2 border-r border-borderSubtle px-3 last:border-r-0">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-textMuted">{label}</span>
      <NumberDisplay value={value} decimals={decimals} suffix={suffix} className="text-sm" />
    </div>
  );
}

function VersionChip() {
  const rawSha = import.meta.env.VITE_GIT_SHA ?? "dev";
  const sha = rawSha.slice(0, 7);
  const rawDate = import.meta.env.VITE_BUILD_DATE ?? "";
  // Accept either an ISO timestamp (CI: github.event.head_commit.timestamp)
  // or a YYYY-MM-DD string. Render as v1.YYYY.MM.DD.
  let datePart = "";
  if (rawDate) {
    const iso = rawDate.length >= 10 ? rawDate.slice(0, 10) : "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
      datePart = iso.replaceAll("-", ".");
    }
  }
  const version = datePart ? `v1.${datePart}` : "v1.dev";
  return (
    <span
      title={`build ${rawSha}${rawDate ? ` · ${rawDate}` : ""}`}
      className="hidden rounded-md border border-borderSubtle bg-bgHover px-2 py-0.5 font-mono text-[10px] tracking-tight text-textMuted md:inline-flex"
    >
      {version} · {sha}
    </span>
  );
}

export function TopBar() {
  const metrics = useMacroStore((s) => s.metrics);
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const wsStatus = useWebSocketStore((s) => s.status);
  const setMobileSidebarOpen = useSettingsStore((s) => s.setMobileSidebarOpen);

  async function logout() {
    try {
      await apiLogout();
    } catch {
      // swallow
    } finally {
      clear();
      window.location.assign("/login");
    }
  }

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-borderSubtle bg-bgSecondary px-3 md:px-4">
      <div className="flex items-center gap-3 md:gap-4">
        <button
          onClick={() => setMobileSidebarOpen(true)}
          aria-label="Open menu"
          className="rounded-md p-1.5 text-textSecondary hover:bg-bgHover hover:text-textPrimary md:hidden"
        >
          <Menu size={20} />
        </button>
        <div className="flex items-center gap-2">
          <div className="h-6 w-6 rounded-sm bg-primary-500" />
          <span className="text-sm font-bold tracking-wide">TradeCore</span>
        </div>
        <LiveIndicator live={wsStatus === "connected"} />
      </div>

      <div className="hidden items-center md:flex">
        <MacroMetric label="DXY" value={metrics.dxy} decimals={3} />
        <MacroMetric label="10Y" value={metrics.us10y} decimals={3} suffix="%" />
        <MacroMetric label="VIX" value={metrics.vix} decimals={2} />
        <MacroMetric label="BTC ETF" value={metrics.btc_etf_flows_usd} decimals={0} />
        <MacroMetric label="SPX" value={metrics.sp500} decimals={2} />
        <MacroMetric label="FED" value={metrics.fed_rate_pct} decimals={2} suffix="%" />
      </div>

      <div className="flex items-center gap-2 md:gap-3">
        <VersionChip />
        <NotificationCenter />
        <div className="hidden items-center gap-2 sm:flex">
          <div className="h-7 w-7 rounded-full bg-primary-subtle text-center text-xs font-semibold leading-7 text-primary-400">
            {(user?.email?.[0] ?? "?").toUpperCase()}
          </div>
          <span className="hidden text-sm text-textSecondary lg:inline">{user?.email}</span>
        </div>
        <button
          onClick={logout}
          aria-label="Sign out"
          className="rounded-md p-1.5 text-textSecondary hover:bg-bgHover hover:text-textPrimary"
        >
          <LogOut size={18} />
        </button>
      </div>
    </header>
  );
}
