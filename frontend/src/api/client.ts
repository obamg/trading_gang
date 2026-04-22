import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { useAuthStore } from "@/stores/authStore";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export const http = axios.create({
  baseURL: API_URL,
  withCredentials: true,
});

// Attach JWT on every request
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// One-shot refresh lock, shared across concurrent 401s
let refreshPromise: Promise<string | null> | null = null;
let proactiveRefreshTimer: ReturnType<typeof setTimeout> | null = null;

export function scheduleProactiveRefresh(expiresIn: number) {
  if (proactiveRefreshTimer) clearTimeout(proactiveRefreshTimer);
  const delayMs = expiresIn * 0.8 * 1000;
  proactiveRefreshTimer = setTimeout(() => {
    refreshAccessToken();
  }, delayMs);
}

export function clearProactiveRefresh() {
  if (proactiveRefreshTimer) {
    clearTimeout(proactiveRefreshTimer);
    proactiveRefreshTimer = null;
  }
}

async function refreshAccessToken(): Promise<string | null> {
  try {
    // Refresh token is sent automatically via httpOnly cookie
    const { data } = await axios.post<{
      access_token: string;
      expires_in: number;
    }>(`${API_URL}/auth/refresh`, {}, { withCredentials: true });
    useAuthStore.getState().setAccessToken(data.access_token, data.expires_in);
    scheduleProactiveRefresh(data.expires_in);
    return data.access_token;
  } catch {
    clearProactiveRefresh();
    useAuthStore.getState().clear();
    return null;
  }
}

http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;
    if (!original || error.response?.status !== 401 || original._retry) {
      return Promise.reject(error);
    }
    if (original.url?.includes("/auth/refresh") || original.url?.includes("/auth/login")) {
      return Promise.reject(error);
    }
    original._retry = true;
    refreshPromise ??= refreshAccessToken().finally(() => {
      refreshPromise = null;
    });
    const newToken = await refreshPromise;
    if (!newToken) return Promise.reject(error);
    original.headers.set("Authorization", `Bearer ${newToken}`);
    return http.request(original);
  },
);
