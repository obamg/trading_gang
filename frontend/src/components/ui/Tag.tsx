import type { PropsWithChildren } from "react";
import { cn } from "./cn";

interface Props {
  onRemove?: () => void;
  className?: string;
}

export function Tag({ onRemove, className, children }: PropsWithChildren<Props>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm bg-bgElevated px-2 py-[2px] text-xs text-textSecondary",
        className,
      )}
    >
      {children}
      {onRemove && (
        <button
          onClick={onRemove}
          aria-label="Remove tag"
          className="text-textMuted hover:text-textPrimary"
        >
          ×
        </button>
      )}
    </span>
  );
}
