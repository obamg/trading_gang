import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { http } from "@/api/client";

type Status = "verifying" | "success" | "error";

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [status, setStatus] = useState<Status>("verifying");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      return;
    }
    let cancelled = false;
    http
      .post("/auth/verify-email", { token })
      .then(() => !cancelled && setStatus("success"))
      .catch(() => !cancelled && setStatus("error"));
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">
        {status === "verifying" && "Verifying your email…"}
        {status === "success" && "Email verified"}
        {status === "error" && "Verification failed"}
      </h2>
      <p className="text-sm text-textSecondary">
        {status === "verifying" && "Hold tight while we confirm your address."}
        {status === "success" && "You're all set. You can sign in now."}
        {status === "error" && "This link may be expired or already used."}
      </p>
      <Link to="/login" className="text-xs text-textSecondary hover:text-textPrimary">
        Back to sign in
      </Link>
    </div>
  );
}
