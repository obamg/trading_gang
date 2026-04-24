import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SettingsState {
  sidebarCollapsed: boolean;
  mobileSidebarOpen: boolean;
  browserNotifications: boolean;
  soundAlerts: boolean;
  watchlist: string[];
  theme: "dark";
  toggleSidebar: () => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setBrowserNotifications: (v: boolean) => void;
  setSoundAlerts: (v: boolean) => void;
  setWatchlist: (w: string[]) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      mobileSidebarOpen: false,
      browserNotifications: false,
      soundAlerts: true,
      watchlist: [],
      theme: "dark",
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setMobileSidebarOpen: (mobileSidebarOpen) => set({ mobileSidebarOpen }),
      setBrowserNotifications: (browserNotifications) => set({ browserNotifications }),
      setSoundAlerts: (soundAlerts) => set({ soundAlerts }),
      setWatchlist: (watchlist) => set({ watchlist }),
    }),
    {
      name: "tradecore-local-settings",
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        browserNotifications: s.browserNotifications,
        soundAlerts: s.soundAlerts,
      }),
    },
  ),
);
