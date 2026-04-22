import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { User } from "@/types/auth";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  tokenExpiresAt: number | null;
  bootstrapped: boolean;
  setAccessToken: (token: string, expiresIn?: number) => void;
  setUser: (u: User | null) => void;
  setBootstrapped: (v: boolean) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      tokenExpiresAt: null,
      bootstrapped: false,
      setAccessToken: (token, expiresIn?) =>
        set({
          accessToken: token,
          tokenExpiresAt: expiresIn ? Date.now() + expiresIn * 1000 : null,
        }),
      setUser: (u) => set({ user: u }),
      setBootstrapped: (v) => set({ bootstrapped: v }),
      clear: () => set({ user: null, accessToken: null, tokenExpiresAt: null }),
    }),
    {
      name: "tradecore-auth",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        accessToken: state.accessToken,
        tokenExpiresAt: state.tokenExpiresAt,
      }),
    },
  ),
);
