import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";

interface Props {
  children: ReactNode;
}

export function ProtectedRoute({ children }: Props) {
  const { accessToken, bootstrapped } = useAuthStore();
  const location = useLocation();

  if (!bootstrapped) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-textSecondary">
        Loading…
      </div>
    );
  }

  if (!accessToken) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
