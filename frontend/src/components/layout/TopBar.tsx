import { LogOut } from "lucide-react";
import { NotificationCenter } from "./NotificationCenter";
import { useMacroStore } from "@/stores/macroStore";
import { useAuthStore } from "@/stores/authStore";
import { apiLogout } from "@/api/auth";
import { NumberDisplay } from "@/components/ui/NumberDisplay";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { useWebSocketStore } from "@/stores/webSocketStore";

function MacroMetric({ label, value, decimals = 2, suffix }: { label: string; value: number | null; decimals?: number; suffix?: string }) {
  return (
    <div className="flex items-center gap-2 px-3 border-r border-borderSubtle last:border-r-0">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-textMuted">{label}</span>
      <NumberDisplay value={value} decimals={decimals} suffix={suffix} className="text-sm" />
    </div>
  );
}

export function TopBar() {
  const metrics = useMacroStore((s) => s.metrics);
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const wsStatus = useWebSocketStore((s) => s.status);

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
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-borderSubtle bg-bgSecondary px-4">
      <div className="flex items-center gap-4">
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

      <div className="flex items-center gap-3">
        <NotificationCenter />
        <div className="flex items-center gap-2">
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
