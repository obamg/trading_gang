import {
  Radar,
  Anchor,
  Flame,
  HeartPulse,
  Globe,
  Gem,
  Shield,
  BookOpen,
  BarChart2,
  Eye,
  Activity,
  Newspaper,
  type LucideIcon,
} from "lucide-react";
import type { ModuleKey } from "@/types/alerts";

export interface ModuleDef {
  key: ModuleKey;
  label: string;
  path: string;
  color: string;
  icon: LucideIcon;
}

export const MODULES: ModuleDef[] = [
  { key: "radarx", label: "RadarX", path: "/radarx", color: "#3B82F6", icon: Radar },
  { key: "whaleradar", label: "WhaleRadar", path: "/whaleradar", color: "#06B6D4", icon: Anchor },
  { key: "liquidmap", label: "LiquidMap", path: "/liquidmap", color: "#F97316", icon: Flame },
  { key: "sentimentpulse", label: "SentimentPulse", path: "/sentiment", color: "#A855F7", icon: HeartPulse },
  { key: "macropulse", label: "MacroPulse", path: "/macro", color: "#6366F1", icon: Globe },
  { key: "gemradar", label: "GemRadar", path: "/gemradar", color: "#10B981", icon: Gem },
  { key: "riskcalc", label: "RiskCalc", path: "/riskcalc", color: "#EAB308", icon: Shield },
  { key: "tradelog", label: "TradeLog", path: "/tradelog", color: "#94A3B8", icon: BookOpen },
  { key: "performancecore", label: "Performance", path: "/performance", color: "#14B8A6", icon: BarChart2 },
  { key: "oracle", label: "Oracle", path: "/oracle", color: "#8B5CF6", icon: Eye },
  { key: "flowpulse", label: "FlowPulse", path: "/flowpulse", color: "#F59E0B", icon: Activity },
  { key: "newspulse", label: "NewsPulse", path: "/newspulse", color: "#EF4444", icon: Newspaper },
];

export const MODULE_BY_KEY: Record<ModuleKey, ModuleDef> = Object.fromEntries(
  MODULES.map((m) => [m.key, m]),
) as Record<ModuleKey, ModuleDef>;
