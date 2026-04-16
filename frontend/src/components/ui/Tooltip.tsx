import { useState, type PropsWithChildren } from "react";
import { cn } from "./cn";

interface Props {
  label: string;
  side?: "top" | "right" | "bottom" | "left";
  className?: string;
}

export function Tooltip({ label, side = "right", className, children }: PropsWithChildren<Props>) {
  const [show, setShow] = useState(false);
  const pos =
    side === "right"
      ? "left-full top-1/2 -translate-y-1/2 ml-2"
      : side === "left"
        ? "right-full top-1/2 -translate-y-1/2 mr-2"
        : side === "top"
          ? "bottom-full left-1/2 -translate-x-1/2 mb-2"
          : "top-full left-1/2 -translate-x-1/2 mt-2";

  return (
    <span
      className={cn("relative inline-flex", className)}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
    >
      {children}
      {show && (
        <span
          role="tooltip"
          className={cn(
            "pointer-events-none absolute z-50 whitespace-nowrap rounded-md border border-borderSubtle",
            "bg-bgElevated px-2 py-1 text-xs text-textPrimary shadow-card",
            pos,
          )}
        >
          {label}
        </span>
      )}
    </span>
  );
}
