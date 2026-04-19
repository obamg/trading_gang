import { http } from "./client";
import type { LoginPayload, RegisterPayload, User } from "@/types/auth";

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export async function apiLogin(payload: LoginPayload): Promise<LoginResponse> {
  const { data } = await http.post<LoginResponse>("/auth/login", payload);
  return data;
}

export async function apiRegister(payload: RegisterPayload): Promise<User> {
  const { data } = await http.post<User>("/auth/register", payload);
  return data;
}

export async function apiMe(): Promise<User> {
  const { data } = await http.get<User>("/auth/me");
  return data;
}

export async function apiLogout(): Promise<void> {
  await http.post("/auth/logout");
}

export async function apiForgotPassword(email: string): Promise<void> {
  await http.post("/auth/forgot-password", { email });
}

export async function apiResetPassword(token: string, newPassword: string): Promise<void> {
  await http.post("/auth/reset-password", { token, new_password: newPassword });
}

export function googleOAuthUrl(): string {
  const base = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
  return `${base}/auth/google`;
}
