import { http } from "./client";

export interface SupportedExchange {
  name: string;
  display_name: string;
  requires_passphrase: boolean;
}

export interface ExchangeCredential {
  id: string;
  exchange: string;
  label: string;
  is_active: boolean;
  permissions: Record<string, unknown> | null;
  validated_at: string | null;
  last_synced_at: string | null;
  last_sync_error: string | null;
  created_at: string;
}

export interface SyncResult {
  ok: boolean;
  fills_fetched?: number;
  trades_paired?: number;
  trades_inserted?: number;
  trades_skipped?: number;
  synced_at?: string;
  reason?: string;
}

export interface CreateCredentialBody {
  exchange: string;
  api_key: string;
  api_secret: string;
  passphrase?: string;
  label?: string;
}

export async function apiSupportedExchanges(): Promise<SupportedExchange[]> {
  const { data } = await http.get<{ items: SupportedExchange[] }>("/exchanges/supported");
  return data.items;
}

export async function apiListCredentials(): Promise<ExchangeCredential[]> {
  const { data } = await http.get<{ items: ExchangeCredential[] }>("/exchanges/credentials");
  return data.items;
}

export async function apiCreateCredential(
  body: CreateCredentialBody,
): Promise<ExchangeCredential> {
  const { data } = await http.post<ExchangeCredential>("/exchanges/credentials", body);
  return data;
}

export async function apiDeleteCredential(id: string): Promise<void> {
  await http.delete(`/exchanges/credentials/${id}`);
}

export async function apiSyncCredential(id: string): Promise<SyncResult> {
  const { data } = await http.post<SyncResult>(`/exchanges/credentials/${id}/sync`);
  return data;
}
