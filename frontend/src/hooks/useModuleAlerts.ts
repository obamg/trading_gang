import { useMemo } from "react";
import { useAlerts } from "./useAlerts";
import type { AlertEvent } from "@/types/alerts";

/**
 * Filter the global WebSocket alert buffer to just the events for a given module.
 * Pages subscribe to this to render their live feed without doing the filter
 * in every component.
 */
export function useModuleAlerts(module: string): AlertEvent[] {
  const alerts = useAlerts();
  return useMemo(
    () => alerts.filter((a) => (a.data?.module as string) === module),
    [alerts, module],
  );
}
