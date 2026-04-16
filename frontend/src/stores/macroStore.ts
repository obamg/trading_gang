import { create } from "zustand";
import type { MacroMetrics } from "@/types/market";

interface MacroState {
  metrics: MacroMetrics;
  updatedAt: number | null;
  setMetrics: (m: MacroMetrics) => void;
}

const EMPTY: MacroMetrics = {
  dxy: null,
  us10y: null,
  vix: null,
  btc_etf_flows_usd: null,
  sp500: null,
  fed_rate_pct: null,
};

export const useMacroStore = create<MacroState>((set) => ({
  metrics: EMPTY,
  updatedAt: null,
  setMetrics: (metrics) => set({ metrics, updatedAt: Date.now() }),
}));
