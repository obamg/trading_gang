import { cn } from "./cn";

export interface Tab {
  key: string;
  label: string;
}

interface Props {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function Tabs({ tabs, active, onChange, className }: Props) {
  return (
    <div className={cn("flex items-center gap-1 border-b border-borderSubtle", className)}>
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={cn(
              "px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              isActive
                ? "border-primary-500 text-textPrimary"
                : "border-transparent text-textSecondary hover:text-textPrimary",
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
