import { cn } from "./cn";

interface Props {
  live?: boolean;
  className?: string;
}

export function LiveIndicator({ live = true, className }: Props) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-semibold uppercase", className)}>
      <span
        className={cn(
          "inline-block h-2 w-2 rounded-full",
          live ? "bg-profit animate-pulseDot" : "bg-textMuted",
        )}
      />
      <span className={cn(live ? "text-profit" : "text-textMuted")}>
        {live ? "Live" : "Offline"}
      </span>
    </span>
  );
}
