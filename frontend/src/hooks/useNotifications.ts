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
      if (!isDivergence && !isListing) continue;

      if (soundEnabled) playAlertSound();
      if (browserNotifs) {
        if (isListing) {
          showListingNotification(data);
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
