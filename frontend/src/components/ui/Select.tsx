import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "./cn";

interface Props extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
}

export const Select = forwardRef<HTMLSelectElement, Props>(
  ({ label, error, id, className, children, ...rest }, ref) => {
    const selectId = id || rest.name;
    return (
      <div className="w-full">
        {label && (
          <label htmlFor={selectId} className="mb-1.5 block text-xs font-medium text-textSecondary">
            {label}
          </label>
        )}
        <select
          ref={ref}
          id={selectId}
          className={cn(
            "w-full rounded-md border bg-bgSecondary px-3 text-sm text-textPrimary h-10",
            "focus:outline-none focus:border-borderStrong focus:shadow-glow",
            error ? "border-loss" : "border-borderDefault",
            className,
          )}
          {...rest}
        >
          {children}
        </select>
        {error && <p className="mt-1 text-xs text-loss">{error}</p>}
      </div>
    );
  },
);
Select.displayName = "Select";
