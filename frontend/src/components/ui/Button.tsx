import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const variants: Record<Variant, string> = {
  primary:
    "bg-primary-500 text-white hover:bg-primary-400 active:bg-primary-600 font-semibold",
  secondary:
    "bg-bgElevated text-textPrimary border border-borderDefault hover:bg-bgHover font-medium",
  ghost: "bg-transparent text-textSecondary hover:bg-bgHover hover:text-textPrimary",
  danger: "bg-loss text-white hover:opacity-90 font-semibold",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-5 text-base",
};

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ variant = "primary", size = "md", loading, className, children, disabled, ...rest }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md transition-all",
        "focus:outline-none focus:shadow-glow",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <span className="h-3 w-3 rounded-full bg-current animate-pulseDot" /> : null}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
