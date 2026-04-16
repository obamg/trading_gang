import { cn } from "./cn";

interface Props {
  className?: string;
  rounded?: "sm" | "md" | "lg" | "full";
}

export function Skeleton({ className, rounded = "md" }: Props) {
  const r =
    rounded === "full" ? "rounded-full" : rounded === "lg" ? "rounded-lg" : rounded === "sm" ? "rounded-sm" : "rounded-md";
  return <div className={cn("skeleton-shimmer animate-shimmer", r, className)} aria-hidden />;
}
