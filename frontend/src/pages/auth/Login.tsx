import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { googleOAuthUrl } from "@/api/auth";
import { useAuth } from "@/hooks/useAuth";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login({ email, password });
    } catch (e) {
      if (axios.isAxiosError(e)) {
        setErr(e.response?.data?.error ?? "Login failed");
      } else {
        setErr("Login failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Sign in</h2>
      <Input
        label="Email"
        type="email"
        autoComplete="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <Input
        label="Password"
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {err && <p className="text-xs text-loss">{err}</p>}
      <Button type="submit" loading={busy}>
        Sign in
      </Button>
      <a
        href={googleOAuthUrl()}
        className="inline-flex h-9 items-center justify-center rounded-md border border-borderDefault bg-bgElevated text-sm font-medium hover:bg-bgHover"
      >
        Continue with Google
      </a>
      <div className="mt-2 flex items-center justify-between text-xs text-textSecondary">
        <Link to="/forgot-password" className="hover:text-textPrimary">
          Forgot password?
        </Link>
        <Link to="/register" className="hover:text-textPrimary">
          Create account
        </Link>
      </div>
    </form>
  );
}
