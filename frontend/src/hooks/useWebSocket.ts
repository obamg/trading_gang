import { useEffect, useRef } from "react";
import { useAuthStore } from "@/stores/authStore";
import { useWebSocketStore } from "@/stores/webSocketStore";

const WS_BASE = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws";

/**
 * Connects to GET /ws?token=<jwt> when an access token exists.
 * Reconnects with exponential backoff (max 30s) on close.
 * Routes incoming events into the ws store.
 */
export function useWebSocket() {
  const token = useAuthStore((s) => s.accessToken);
  const setStatus = useWebSocketStore((s) => s.setStatus);
  const pushAlert = useWebSocketStore((s) => s.pushAlert);
  const wsRef = useRef<WebSocket | null>(null);
  const pingRef = useRef<number | null>(null);
  const retryRef = useRef(0);
  const stoppedRef = useRef(false);

  useEffect(() => {
    stoppedRef.current = false;
    if (!token) {
      setStatus("disconnected");
      return () => {
        stoppedRef.current = true;
      };
    }

    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting");
      const ws = new WebSocket(`${WS_BASE}?token=${encodeURIComponent(token)}`);
      wsRef.current = ws;

      ws.onopen = () => {
        retryRef.current = 0;
        setStatus("connected");
        pingRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 30_000);
      };
      ws.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data);
          if (payload?.type && payload.type !== "pong" && payload.type !== "connected") {
            pushAlert(payload);
          }
        } catch {
          // ignore non-JSON frames
        }
      };
      ws.onerror = () => setStatus("error");
      ws.onclose = () => {
        if (pingRef.current !== null) {
          window.clearInterval(pingRef.current);
          pingRef.current = null;
        }
        setStatus("disconnected");
        if (!stoppedRef.current && !cancelled) {
          const delay = Math.min(1000 * 2 ** retryRef.current, 30_000);
          retryRef.current += 1;
          window.setTimeout(connect, delay);
        }
      };
    };

    connect();

    return () => {
      cancelled = true;
      stoppedRef.current = true;
      if (pingRef.current !== null) window.clearInterval(pingRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [token, setStatus, pushAlert]);
}
