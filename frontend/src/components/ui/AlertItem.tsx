import type { PropsWithChildren, ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import { cn } from "./cn";
import { Badge } from "./Badge";

interface Props {
  symbol: string;
  accentColor: string; // module accent
  moduleLabel: string;
  timestamp: string;
  stats?: ReactNode;
  context?: ReactNode;
  unread?: boolean;
  chartUrl?: string;
  onAction?: () => void;
  actionLabel?: string;
  className?: string;
}

export function AlertItem({
  symbol,
  accentColor,
  moduleLabel,
  timestamp,
  stats,
  context,
  unread,
  chartUrl,
  onAction,
  actionLabel,
  className,
  children,
}: PropsWithChildren<Props>) {
  return (
    <div
      className={cn(
        "rounded-lg border border-borderSubtle bg-bgCard shadow-card transition-colors",
        "border-l-[3px]",
        unread && "bg-bgElevated",
        "animate-slideDown",
        className,
      )}
      style={{ borderLeftColor: accentColor }}
    >
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 flex-col">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{symbol}</span>
            <Badge variant="module" accentColor={accentColor}>
              {moduleLabel}
            </Badge>
          </div>
          {stats && <div className="mt-1 text-sm text-textSecondary">{stats}</div>}
          {context && <div className="mt-0.5 text-xs text-textMuted">{context}</div>}
          {children}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-xs text-textMuted">{timestamp}</span>
          <div className="flex items-center gap-1">
            {onAction && actionLabel && (
              <button
                onClick={onAction}
                className="rounded-md bg-bgElevated px-2 py-1 text-xs font-medium text-textPrimary hover:bg-bgHover"
              >
                {actionLabel}
              </button>
            )}
            {chartUrl && (
              <a
                href={chartUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-md bg-bgElevated px-2 py-1 text-xs text-textSecondary hover:bg-bgHover hover:text-textPrimary"
              >
                Chart <ExternalLink size={12} />
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
