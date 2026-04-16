import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";

export default function Register() {
  const { register } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [terms, setTerms] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!terms) {
      setErr("Please accept the terms to continue.");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      await register({ email, password, full_name: fullName || undefined });
    } catch (e) {
      if (axios.isAxiosError(e)) {
        setErr(e.response?.data?.error ?? "Registration failed");
      } else {
        setErr("Registration failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Create account</h2>
      <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
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
        autoComplete="new-password"
        required
        minLength={8}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <label className="flex items-center gap-2 text-xs text-textSecondary">
        <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
        I agree to the Terms and Privacy Policy.
      </label>
      {err && <p className="text-xs text-loss">{err}</p>}
      <Button type="submit" loading={busy}>
        Create account
      </Button>
      <Link to="/login" className="text-center text-xs text-textSecondary hover:text-textPrimary">
        Already have an account? Sign in
      </Link>
    </form>
  );
}
