import type { PropsWithChildren } from "react";
import { cn } from "./cn";

export type CardVariant = "default" | "alert" | "active" | "danger";

interface CardProps {
  variant?: CardVariant;
  accentColor?: string; // used for variant="alert" left border
  className?: string;
}

const base =
  "rounded-lg border shadow-card bg-bgCard border-borderSubtle transition-colors";

export function Card({
  variant = "default",
  accentColor,
  className,
  children,
}: PropsWithChildren<CardProps>) {
  const style =
    variant === "alert" && accentColor
      ? { borderLeftColor: accentColor, borderLeftWidth: "3px" }
      : undefined;

  return (
    <div
      className={cn(
        base,
        variant === "active" && "border-primary-500/50 shadow-glow",
        variant === "danger" && "border-l-[3px] border-l-loss",
        variant === "alert" && "border-l-[3px]",
        className,
      )}
      style={style}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className }: PropsWithChildren<{ className?: string }>) {
  return (
    <div className={cn("flex items-center justify-between px-4 py-3 border-b border-borderSubtle", className)}>
      {children}
    </div>
  );
}

export function CardBody({ children, className }: PropsWithChildren<{ className?: string }>) {
  return <div className={cn("p-4", className)}>{children}</div>;
}
