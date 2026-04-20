import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthLayout } from "@/components/layout/AuthLayout";
import { AppLayout } from "@/components/layout/AppLayout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import Login from "@/pages/auth/Login";
import Register from "@/pages/auth/Register";
import ForgotPassword from "@/pages/auth/ForgotPassword";
import ResetPassword from "@/pages/auth/ResetPassword";
import VerifyEmail from "@/pages/auth/VerifyEmail";
import Dashboard from "@/pages/dashboard";
import RadarXPage from "@/pages/dashboard/radarx";
import WhaleRadarPage from "@/pages/dashboard/whaleradar";
import LiquidMapPage from "@/pages/dashboard/liquidmap";
import SentimentPage from "@/pages/dashboard/sentiment";
import MacroPage from "@/pages/dashboard/macro";
import GemRadarPage from "@/pages/dashboard/gemradar";
import RiskCalcPage from "@/pages/dashboard/riskcalc";
import TradeLogPage from "@/pages/dashboard/tradelog";
import PerformancePage from "@/pages/dashboard/performance";
import OraclePage from "@/pages/dashboard/oracle";
import LandingPage from "@/pages/landing";
import { useAuthStore } from "@/stores/authStore";
import { apiMe } from "@/api/auth";
import { useWebSocket } from "@/hooks/useWebSocket";

function AuthBootstrap({ children }: { children: React.ReactNode }) {
  const { accessToken, setUser, setBootstrapped, clear, bootstrapped } = useAuthStore();

  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!accessToken) {
        setBootstrapped(true);
        return;
      }
      try {
        const me = await apiMe();
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) clear();
      } finally {
        if (!cancelled) setBootstrapped(true);
      }
    }
    if (!bootstrapped) run();
    return () => {
      cancelled = true;
    };
  }, [accessToken, bootstrapped, setUser, setBootstrapped, clear]);

  return <>{children}</>;
}

function WebSocketMount() {
  useWebSocket();
  return null;
}

function RootRoute() {
  const { accessToken } = useAuthStore();
  return accessToken ? <Navigate to="/dashboard" replace /> : <LandingPage />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthBootstrap>
        <WebSocketMount />
        <Routes>
          <Route path="/" element={<RootRoute />} />

          <Route element={<AuthLayout />}>
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/verify-email" element={<VerifyEmail />} />
          </Route>

          <Route
            element={
              <ProtectedRoute>
                <AppLayout />
              </ProtectedRoute>
            }
          >
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/radarx" element={<RadarXPage />} />
            <Route path="/whaleradar" element={<WhaleRadarPage />} />
            <Route path="/liquidmap" element={<LiquidMapPage />} />
            <Route path="/sentiment" element={<SentimentPage />} />
            <Route path="/macro" element={<MacroPage />} />
            <Route path="/gemradar" element={<GemRadarPage />} />
            <Route path="/riskcalc" element={<RiskCalcPage />} />
            <Route path="/tradelog" element={<TradeLogPage />} />
            <Route path="/performance" element={<PerformancePage />} />
            <Route path="/oracle" element={<OraclePage />} />
            <Route path="/settings" element={<Placeholder label="Settings" />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthBootstrap>
    </BrowserRouter>
  );
}

function Placeholder({ label }: { label: string }) {
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold">{label}</h1>
      <p className="mt-2 text-sm text-textSecondary">
        Module scaffold — implementation lands in its dedicated team build.
      </p>
    </div>
  );
}
