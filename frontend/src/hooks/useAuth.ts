import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { apiLogin, apiRegister, apiLogout, apiMe } from "@/api/auth";
import { scheduleProactiveRefresh, clearProactiveRefresh } from "@/api/client";
import { useAuthStore } from "@/stores/authStore";
import type { LoginPayload, RegisterPayload } from "@/types/auth";

export function useAuth() {
  const navigate = useNavigate();
  const { setAccessToken, setUser, clear } = useAuthStore();

  const login = useCallback(
    async (payload: LoginPayload) => {
      const tokens = await apiLogin(payload);
      setAccessToken(tokens.access_token, tokens.expires_in);
      scheduleProactiveRefresh(tokens.expires_in);
      const user = await apiMe();
      setUser(user);
      navigate("/");
    },
    [navigate, setAccessToken, setUser],
  );

  const register = useCallback(
    async (payload: RegisterPayload) => {
      await apiRegister(payload);
      await login({ email: payload.email, password: payload.password });
    },
    [login],
  );

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // Best-effort — clear local state regardless
    }
    clearProactiveRefresh();
    clear();
    navigate("/login");
  }, [clear, navigate]);

  return { login, register, logout };
}
