import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { useWebSocketStore } from "@/stores/webSocketStore";
import type { AlertEvent } from "@/types/alerts";

const MODULE_COLORS: Record<string, string> = {
  radarx: "#f59e0b",
  whaleradar: "#3b82f6",
  liquidmap: "#ef4444",
  gemradar: "#8b5cf6",
  oracle: "#10b981",
  sentimentpulse: "#6366f1",
  macropulse: "#ec4899",
};

function moduleFromType(type: string): string {
  return type.replace(/_alert$/, "");
}

function formatTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function AlertRow({ alert }: { alert: AlertEvent }) {
  const module = moduleFromType(alert.type);
  const data = alert.data ?? {};
  const symbol = (data.symbol as string) ?? "—";
  const color = MODULE_COLORS[module] ?? "#9ca3af";

  let detail = "";
  if (module === "radarx") {
    detail = `Z: ${data.z_score ?? "?"} | ${data.ratio ?? "?"}×`;
  } else if (module === "whaleradar") {
    const usd = Number(data.trade_size_usd ?? data.oi_change_pct ?? 0);
    detail = data.type === "oi_surge"
      ? `OI ${usd > 0 ? "+" : ""}${usd.toFixed(1)}%`
      : `$${(usd / 1000).toFixed(0)}K ${data.side ?? ""}`;
  } else if (module === "gemradar") {
    detail = `MCap $${((Number(data.market_cap_usd) || 0) / 1e6).toFixed(1)}M`;
  } else if (module === "oracle") {
    detail = `Score: ${data.score ?? "?"}`;
  } else if (module === "liquidmap") {
    detail = `$${((Number(data.size_usd) || 0) / 1000).toFixed(0)}K ${data.side ?? ""}`;
  }

  return (
    <div className="flex items-start gap-3 px-4 py-3 hover:bg-bgHover transition-colors border-b border-borderSubtle last:border-b-0">
      <div className="mt-1 h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">{symbol}</span>
          <span className="shrink-0 text-[10px] text-textMuted">{formatTime(alert.receivedAt)}</span>
        </div>
        <div className="text-xs text-textSecondary">
          <span className="font-medium" style={{ color }}>{module}</span>
          {detail && <span className="ml-1.5">{detail}</span>}
        </div>
      </div>
    </div>
  );
}

export function NotificationCenter() {
  const alerts = useWebSocketStore((s) => s.alerts);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  const unread = alerts.length - seen;

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function toggle() {
    setOpen((v) => !v);
    if (!open) setSeen(alerts.length);
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={toggle}
        aria-label="Notifications"
        className="relative rounded-md p-1.5 text-textSecondary hover:bg-bgHover hover:text-textPrimary"
      >
        <Bell size={18} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-accent-red px-1 text-[10px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="fixed inset-x-3 top-14 z-50 overflow-hidden rounded-lg border border-borderSubtle bg-bgCard shadow-xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-80">
          <div className="flex items-center justify-between border-b border-borderSubtle px-4 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            {alerts.length > 0 && (
              <button
                onClick={() => setSeen(alerts.length)}
                className="text-xs text-primary-400 hover:text-primary-300"
              >
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {alerts.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-textMuted">
                No alerts yet
              </div>
            ) : (
              alerts.map((a, i) => <AlertRow key={`${a.type}-${a.receivedAt}-${i}`} alert={a} />)
            )}
          </div>
        </div>
      )}
    </div>
  );
}
