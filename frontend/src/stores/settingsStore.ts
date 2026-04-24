import { create } from "zustand";

interface SettingsState {
  sidebarCollapsed: boolean;
  mobileSidebarOpen: boolean;
  watchlist: string[];
  theme: "dark";
  toggleSidebar: () => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setWatchlist: (w: string[]) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  sidebarCollapsed: false,
  mobileSidebarOpen: false,
  watchlist: [],
  theme: "dark",
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setMobileSidebarOpen: (mobileSidebarOpen) => set({ mobileSidebarOpen }),
  setWatchlist: (watchlist) => set({ watchlist }),
}));
