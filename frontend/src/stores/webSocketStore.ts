import { create } from "zustand";
import type { AlertEvent } from "@/types/alerts";

type Status = "disconnected" | "connecting" | "connected" | "error";

const MAX_ALERTS = 50;

interface WSState {
  status: Status;
  alerts: AlertEvent[];
  setStatus: (s: Status) => void;
  pushAlert: (e: AlertEvent) => void;
  clear: () => void;
}

export const useWebSocketStore = create<WSState>((set) => ({
  status: "disconnected",
  alerts: [],
  setStatus: (status) => set({ status }),
  pushAlert: (e) =>
    set((s) => ({ alerts: [{ ...e, receivedAt: Date.now() }, ...s.alerts].slice(0, MAX_ALERTS) })),
  clear: () => set({ alerts: [], status: "disconnected" }),
}));
