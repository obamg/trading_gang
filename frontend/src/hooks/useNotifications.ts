import { useEffect, useRef } from "react";
import { useWebSocketStore } from "@/stores/webSocketStore";
import { useSettingsStore } from "@/stores/settingsStore";

let audioCtx: AudioContext | null = null;

function playAlertSound() {
  try {
    if (!audioCtx) audioCtx = new AudioContext();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);

    osc.type = "sine";
    gain.gain.setValueAtTime(0.3, audioCtx.currentTime);

    // Two-tone alert: rising pitch
    osc.frequency.setValueAtTime(600, audioCtx.currentTime);
    osc.frequency.setValueAtTime(900, audioCtx.currentTime + 0.12);
    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);

    osc.start(audioCtx.currentTime);
    osc.stop(audioCtx.currentTime + 0.3);
  } catch {
    // Audio not available
  }
}

function showBrowserNotification(symbol: string, zScore: number, pricePct: number, divScore: number) {
  if (Notification.permission !== "granted") return;

  const body = `Z: ${zScore} | Price: ${pricePct > 0 ? "+" : ""}${pricePct.toFixed(2)}% | Div: ${divScore}`;

  const n = new Notification(`⚡ Divergence — ${symbol}`, {
    body,
    icon: "/favicon.ico",
    tag: `div-${symbol}`,
    requireInteraction: false,
  });

  setTimeout(() => n.close(), 8000);
}

function showListingNotification(data: Record<string, unknown>) {
  if (Notification.permission !== "granted") return;

  const symbol = (data.symbol as string) || "?";
  const exchange = ((data.exchange as string) || "?").toUpperCase();
  const market = (data.market_type as string) || "";
  const subType = (data.type as string) || "";

  let title: string;
  let body: string;
  let tag: string;

  if (subType === "listing_detected") {
    const others = (data.other_exchanges as string[]) || [];
    const cross = data.is_cross_listing && others.length
      ? ` (also on ${others.map((o) => o.toUpperCase()).join(", ")})`
      : "";
    const innovation = data.innovation ? " 🚀 Innovation Zone" : "";
    title = `🆕 New Listing — ${symbol}${innovation}`;
    body = `${exchange} ${market}${cross}`.trim();
    tag = `listing-${symbol}`;
  } else {
    const direction = ((data.direction as string) || "").toUpperCase();
    const conv = data.conviction;
    const convStr = typeof conv === "number" ? conv.toFixed(2) : "?";
    const label = subType.replace(/_/g, " ");
    title = `✨ ListingWatch — ${symbol}`;
    body = `${label}${direction ? ` (${direction})` : ""} | Conviction: ${convStr}`;
    tag = `listing-${symbol}-${subType}`;
  }

  const n = new Notification(title, {
    body,
    icon: "/favicon.ico",
    tag,
    requireInteraction: false,
  });

  setTimeout(() => n.close(), 8000);
}

function showWavewatchNotification(data: Record<string, unknown>) {
  if (Notification.permission !== "granted") return;

  const symbol = (data.symbol as string) || "?";
  const base = (data.base_asset as string) || symbol;
  const market = ((data.market_type as string) || "").toUpperCase();
  const subtype = (data.type as string) || "wave_incoming";

  let title: string;
  let body: string;

  if (subtype === "wave_active") {
    // Cascade / squeeze — directional, real-time
    const direction = (data.direction as string) || "?";
    const pct =
      typeof data.pct_change === "number"
        ? `${data.pct_change >= 0 ? "+" : ""}${(data.pct_change * 100).toFixed(2)}%`
        : "?";
    const volX =
      typeof data.vol_ratio === "number" ? `${(data.vol_ratio as number).toFixed(1)}×` : "?";
    const fundingPct =
      typeof data.funding_pct === "number"
        ? `${((data.funding_pct as number) * 100).toFixed(3)}%`
        : "—";
    const icon = direction === "short_squeeze" ? "🚀" : direction === "long_flush" ? "🔻" : "⚡";
    title = `${icon} WaveActive — ${base}`;
    body = `${market} | ${direction.replace("_", " ")} | ${pct} | Vol ${volX} | Fund ${fundingPct}`;
  } else {
    // Pre-wave coiling — the original signal
    const score = typeof data.score === "number" ? data.score.toFixed(2) : "?";
    const volX =
      typeof data.vol_ratio_now === "number" ? `${(data.vol_ratio_now as number).toFixed(1)}×` : "?";
    const dwellMin =
      typeof data.dwell_seconds === "number" ? `${Math.floor((data.dwell_seconds as number) / 60)}m` : "?";
    title = `🌊 WaveWatch — ${base}`;
    body = `${market} | Score ${score} | Vol ${volX} | Dwell ${dwellMin}`;
  }

  const n = new Notification(title, {
    body,
    icon: "/favicon.ico",
    tag: `wavewatch-${symbol}-${subtype}`,
    requireInteraction: false,
  });

  setTimeout(() => n.close(), 8000);
}

function showAwakeningNotification(data: Record<string, unknown>) {
  if (Notification.permission !== "granted") return;

  const symbol = (data.symbol as string) || "?";
  const exchange = ((data.exchange as string) || "?").toUpperCase();
  const ratio = typeof data.ratio === "number" ? `${data.ratio.toFixed(1)}×` : "?";
  const pct = typeof data.price_change_pct === "number"
    ? `${data.price_change_pct >= 0 ? "+" : ""}${data.price_change_pct.toFixed(2)}%`
    : "?";

  const n = new Notification(`🌅 Awakening — ${symbol}`, {
    body: `${exchange} | ${ratio} vs 7d baseline | 24h ${pct}`,
    icon: "/favicon.ico",
    tag: `awakening-${symbol}`,
    requireInteraction: false,
  });

  setTimeout(() => n.close(), 8000);
}

export function useNotifications() {
  const alerts = useWebSocketStore((s) => s.alerts);
  const browserNotifs = useSettingsStore((s) => s.browserNotifications);
  const soundEnabled = useSettingsStore((s) => s.soundAlerts);
  const lastCountRef = useRef(alerts.length);

  useEffect(() => {
    if (!browserNotifs && !soundEnabled) return;

    const prevCount = lastCountRef.current;
    lastCountRef.current = alerts.length;

    if (alerts.length <= prevCount) return;

    const newAlerts = alerts.slice(0, alerts.length - prevCount);

    for (const alert of newAlerts) {
      const data = alert.data;
      if (!data) continue;

      const isDivergence = Boolean(data.is_divergence);
      const isListing = alert.type === "listingwatch_alert";
      const isAwakening = alert.type === "awakening_alert";
      const isWavewatch = alert.type === "wavewatch_alert";
      if (!isDivergence && !isListing && !isAwakening && !isWavewatch) continue;

      if (soundEnabled) playAlertSound();
      if (browserNotifs) {
        if (isListing) {
          showListingNotification(data);
        } else if (isAwakening) {
          showAwakeningNotification(data);
        } else if (isWavewatch) {
          showWavewatchNotification(data);
        } else {
          showBrowserNotification(
            data.symbol as string,
            data.z_score as number,
            data.price_change_pct as number,
            data.divergence_score as number,
          );
        }
      }
      break; // one notification per batch
    }
  }, [alerts, browserNotifs, soundEnabled]);
}

export async function requestNotificationPermission(): Promise<boolean> {
  if (!("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  return result === "granted";
}
