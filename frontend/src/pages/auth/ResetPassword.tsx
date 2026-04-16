import { useState, type FormEvent } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import axios from "axios";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { apiResetPassword } from "@/api/auth";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setErr("Passwords do not match.");
      return;
    }
    if (!token) {
      setErr("Reset link is invalid or expired.");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      await apiResetPassword(token, password);
      setDone(true);
      setTimeout(() => navigate("/login"), 2000);
    } catch (e) {
      if (axios.isAxiosError(e)) {
        setErr(e.response?.data?.error ?? "Reset failed");
      } else {
        setErr("Reset failed");
      }
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Password updated</h2>
        <p className="text-sm text-textSecondary">Redirecting you to sign in…</p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Choose a new password</h2>
      <Input
        label="New password"
        type="password"
        autoComplete="new-password"
        required
        minLength={8}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <Input
        label="Confirm password"
        type="password"
        autoComplete="new-password"
        required
        minLength={8}
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
      />
      {err && <p className="text-xs text-loss">{err}</p>}
      <Button type="submit" loading={busy}>
        Update password
      </Button>
      <Link to="/login" className="text-center text-xs text-textSecondary hover:text-textPrimary">
        Back to sign in
      </Link>
    </form>
  );
}
