import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "./cn";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, Props>(
  ({ label, error, id, className, ...rest }, ref) => {
    const inputId = id || rest.name;
    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="mb-1.5 block text-xs font-medium text-textSecondary">
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={cn(
            "w-full rounded-md border bg-bgSecondary px-3 text-sm text-textPrimary h-10",
            "placeholder:text-textMuted",
            "focus:outline-none focus:border-borderStrong focus:shadow-glow",
            error ? "border-loss" : "border-borderDefault",
            className,
          )}
          {...rest}
        />
        {error && <p className="mt-1 text-xs text-loss">{error}</p>}
      </div>
    );
  },
);
Input.displayName = "Input";
