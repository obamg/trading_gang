import type { PropsWithChildren } from "react";
import { cn } from "./cn";

type Variant = "bullish" | "bearish" | "warning" | "neutral" | "new" | "module";

interface Props {
  variant?: Variant;
  accentColor?: string; // for variant="module"
  className?: string;
}

const styles: Record<Variant, string> = {
  bullish: "bg-profit-subtle text-profit",
  bearish: "bg-loss-subtle text-loss",
  warning: "bg-warning-subtle text-warning",
  neutral: "bg-bgElevated text-textMuted",
  new: "bg-primary-subtle text-primary-400",
  module: "",
};

export function Badge({ variant = "neutral", accentColor, className, children }: PropsWithChildren<Props>) {
  const style =
    variant === "module" && accentColor
      ? { color: accentColor, backgroundColor: `${accentColor}22` }
      : undefined;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-2 py-[2px] text-xs font-semibold uppercase tracking-wide",
        styles[variant],
        className,
      )}
      style={style}
    >
      {children}
    </span>
  );
}
