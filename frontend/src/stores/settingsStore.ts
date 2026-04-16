import { create } from "zustand";

interface SettingsState {
  sidebarCollapsed: boolean;
  watchlist: string[];
  theme: "dark";
  toggleSidebar: () => void;
  setWatchlist: (w: string[]) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  sidebarCollapsed: false,
  watchlist: [],
  theme: "dark",
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setWatchlist: (watchlist) => set({ watchlist }),
}));
