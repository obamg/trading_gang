import { Outlet } from "react-router-dom";

export function AuthLayout() {
  return (
    <div className="flex min-h-full items-center justify-center bg-canvas p-4 sm:p-6">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="h-8 w-8 rounded-sm bg-primary-500" />
          <span className="text-lg font-bold tracking-wide">TradeCore</span>
        </div>
        <div className="rounded-xl border border-borderSubtle bg-bgCard p-5 shadow-card sm:p-8">
          <Outlet />
        </div>
        <p className="mt-4 text-center text-xs text-textMuted">
          Built for traders who make decisions under pressure.
        </p>
      </div>
    </div>
  );
}
