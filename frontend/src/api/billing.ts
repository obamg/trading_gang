import { http } from "./client";

export interface SubscriptionInfo {
  plan: string;
  status: string;
  billing_cycle: string;
  current_period_end: string | null;
}

export async function apiCreateCheckout(plan: string, cycle: string): Promise<{ checkout_url: string }> {
  const { data } = await http.post<{ checkout_url: string; session_id: string }>(
    "/billing/checkout",
    { plan, billing_cycle: cycle },
  );
  return data;
}

export async function apiPortal(): Promise<{ url: string }> {
  const { data } = await http.post<{ url: string }>("/billing/portal");
  return data;
}

export async function apiSubscription(): Promise<SubscriptionInfo | null> {
  const { data } = await http.get<SubscriptionInfo | null>("/billing/subscription");
  return data;
}
