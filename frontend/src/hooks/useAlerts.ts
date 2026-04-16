import { useWebSocketStore } from "@/stores/webSocketStore";

export function useAlerts() {
  return useWebSocketStore((s) => s.alerts);
}
