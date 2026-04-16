import { create } from "zustand";
import type { User } from "@/types/auth";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  // Refresh token is held in memory for now; backend will later issue it
  // as an httpOnly cookie (not yet configured server-side).
  refreshToken: string | null;
  bootstrapped: boolean;
  setTokens: (access: string, refresh: string) => void;
  setUser: (u: User | null) => void;
  setBootstrapped: (v: boolean) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  bootstrapped: false,
  setTokens: (access, refresh) => set({ accessToken: access, refreshToken: refresh }),
  setUser: (u) => set({ user: u }),
  setBootstrapped: (v) => set({ bootstrapped: v }),
  clear: () => set({ user: null, accessToken: null, refreshToken: null }),
}));
