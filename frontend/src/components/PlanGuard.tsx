import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

export type PlanTier = "free" | "starter" | "pro" | "elite";

const TIER_ORDER: PlanTier[] = ["free", "starter", "pro", "elite"];

interface Props {
  currentTier: PlanTier;
  requiredTier: PlanTier;
  moduleName: string;
  children: ReactNode;
}

export function PlanGuard({ currentTier, requiredTier, moduleName, children }: Props) {
  const has = TIER_ORDER.indexOf(currentTier) >= TIER_ORDER.indexOf(requiredTier);
  if (has) return <>{children}</>;

  return (
    <Card>
      <CardBody className="flex flex-col items-start gap-3">
        <h3 className="text-base font-semibold">{moduleName} is on the {requiredTier} plan</h3>
        <p className="text-sm text-textSecondary">
          Upgrade your subscription to unlock this module and its live alerts.
        </p>
        <Link to="/billing">
          <Button>Upgrade plan</Button>
        </Link>
      </CardBody>
    </Card>
  );
}
