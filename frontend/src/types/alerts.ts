export type ModuleKey =
  | "radarx"
  | "whaleradar"
  | "liquidmap"
  | "sentimentpulse"
  | "macropulse"
  | "gemradar"
  | "riskcalc"
  | "tradelog"
  | "performancecore"
  | "oracle";

export interface AlertEvent {
  type: string;
  data: Record<string, unknown>;
  receivedAt?: number;
}

export interface PriceUpdate {
  type: "price_update";
  symbol: string;
  price: number;
}
