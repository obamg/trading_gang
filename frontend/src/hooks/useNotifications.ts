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
      if (!data?.is_divergence) continue;

      if (soundEnabled) playAlertSound();
      if (browserNotifs) {
        showBrowserNotification(
          data.symbol as string,
          data.z_score as number,
          data.price_change_pct as number,
          data.divergence_score as number,
        );
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
