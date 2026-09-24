import { http } from "./client";

export interface UserSettings {
  telegram_enabled: boolean;
  telegram_linked: boolean;
  radarx_zscore_threshold: number;
  radarx_ratio_threshold: number;
  radarx_min_volume_usd: number;
  radarx_cooldown_minutes: number;
  whaleradar_min_trade_usd: number;
  whaleradar_min_onchain_usd: number;
  gemradar_min_mcap_usd: number;
  gemradar_max_mcap_usd: number;
  oracle_min_score: number;
  oracle_min_confluence: number;
}

export async function apiGetSettings(): Promise<UserSettings> {
  const { data } = await http.get<UserSettings>("/settings");
  return data;
}

export async function apiUpdateSettings(body: Partial<UserSettings>): Promise<UserSettings> {
  const { data } = await http.patch<UserSettings>("/settings", body);
  return data;
}

export type TelegramLinkInfo = {
  token: string;
  /** One-tap `t.me` URL with the code already attached. Null when the bot
   *  hasn't resolved its own username — fall back to showing `token`. */
  deep_link: string | null;
  expires_in_seconds: number;
};

export async function apiCreateTelegramToken(): Promise<TelegramLinkInfo> {
  const { data } = await http.post<TelegramLinkInfo>("/settings/telegram/link-token");
  return data;
}

export async function apiUnlinkTelegram(): Promise<void> {
  await http.delete("/settings/telegram");
}
