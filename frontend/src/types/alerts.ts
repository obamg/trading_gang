export type ModuleKey =
  | "radarx"
  | "whaleradar"
  | "liquidmap"
  | "sentimentpulse"
  | "macropulse"
  | "riskcalc"
  | "performancecore"
  | "oracle"
  | "flowpulse"
  | "newspulse"
  | "walletwatch"
  | "listingwatch"
  | "awakening"
  | "wavewatch";

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
