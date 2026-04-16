import { useEffect, type PropsWithChildren } from "react";
import { X } from "lucide-react";
import { cn } from "./cn";

interface Props {
  open: boolean;
  onClose: () => void;
  title?: string;
  className?: string;
}

export function Modal({ open, onClose, title, className, children }: PropsWithChildren<Props>) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className={cn(
          "relative w-full max-w-md rounded-xl border border-borderSubtle bg-bgElevated shadow-card",
          "animate-slideDown",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-borderSubtle px-5 py-3">
          <h3 className="text-md font-semibold">{title}</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-textMuted hover:text-textPrimary"
          >
            <X size={18} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
