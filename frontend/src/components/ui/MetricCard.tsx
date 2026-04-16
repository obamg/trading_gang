import type { ReactNode } from "react";
import { cn } from "./cn";
import { NumberDisplay } from "./NumberDisplay";
import { PercentChange } from "./PercentChange";

interface Props {
  label: string;
  value: number | null;
  valueDecimals?: number;
  valuePrefix?: string;
  valueSuffix?: string;
  change?: number | null;
  icon?: ReactNode;
  className?: string;
  compact?: boolean;
}

export function MetricCard({
  label,
  value,
  valueDecimals = 2,
  valuePrefix,
  valueSuffix,
  change,
  icon,
  className,
  compact = false,
}: Props) {
  return (
    <div
      className={cn(
        "rounded-lg border border-borderSubtle bg-bgCard",
        compact ? "px-3 py-2" : "p-4",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs uppercase tracking-wide text-textMuted">{label}</span>
        {icon}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <NumberDisplay
          value={value}
          decimals={valueDecimals}
          prefix={valuePrefix}
          suffix={valueSuffix}
          className={cn(compact ? "text-md font-semibold" : "text-xl font-bold")}
        />
        {change !== undefined && change !== null ? (
          <PercentChange value={change} className="text-xs" />
        ) : null}
      </div>
    </div>
  );
}
