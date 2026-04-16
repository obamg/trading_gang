import { cn } from "./cn";

interface Props {
  value: number | null | undefined;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  colored?: boolean; // green if positive, red if negative
  sign?: boolean; // force + on positives
  className?: string;
}

const MINUS = "\u2212"; // real minus sign

export function NumberDisplay({
  value,
  decimals = 2,
  prefix = "",
  suffix = "",
  colored = false,
  sign = false,
  className,
}: Props) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={cn("font-data text-textMuted", className)}>—</span>;
  }
  const positive = value > 0;
  const negative = value < 0;
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const prefixChar = negative ? MINUS : sign && positive ? "+" : "";

  return (
    <span
      className={cn(
        "font-data tabular-nums",
        colored && positive && "text-profit",
        colored && negative && "text-loss",
        colored && value === 0 && "text-textMuted",
        className,
      )}
    >
      {prefixChar}
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
}
