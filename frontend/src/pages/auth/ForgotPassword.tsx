import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { apiForgotPassword } from "@/api/auth";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await apiForgotPassword(email);
    } finally {
      setBusy(false);
      setSent(true);
    }
  }

  if (sent) {
    return (
      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Check your email</h2>
        <p className="text-sm text-textSecondary">
          If an account exists for <span className="font-data">{email}</span>, a reset link is on its way.
        </p>
        <Link to="/login" className="text-xs text-textSecondary hover:text-textPrimary">
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Reset password</h2>
      <Input
        label="Email"
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <Button type="submit" loading={busy}>
        Send reset link
      </Button>
      <Link to="/login" className="text-center text-xs text-textSecondary hover:text-textPrimary">
        Back to sign in
      </Link>
    </form>
  );
}
