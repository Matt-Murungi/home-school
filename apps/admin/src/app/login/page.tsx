import { safeNext } from "@/lib/access";
import { EnvBadge } from "@/components/EnvBadge";
import { loadConfig } from "@/lib/config";

type SP = { step?: string; error?: string; next?: string; expired?: string };

export default async function LoginPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const next = safeNext(sp.next);
  const mfa = sp.step === "mfa";
  return (
    <main className="login">
      <EnvBadge label={loadConfig().envLabel} />
      <h1>{mfa ? "Enter your authentication code" : "Admin sign in"}</h1>
      {sp.expired && <p role="status">Your session timed out. Sign in again.</p>}
      {sp.error && (
        <p role="alert" className="field-error">
          {sp.error}
        </p>
      )}
      <form action="/api/session" method="post">
        <input type="hidden" name="next" value={next} />
        {mfa ? (
          <>
            <input type="hidden" name="step" value="mfa" />
            <label htmlFor="code">6-digit code</label>
            <input
              id="code"
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              required
            />
          </>
        ) : (
          <>
            <input type="hidden" name="step" value="password" />
            <label htmlFor="email">Email</label>
            <input id="email" name="email" type="email" autoComplete="username" required />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </>
        )}
        <button type="submit">{mfa ? "Verify" : "Continue"}</button>
      </form>
    </main>
  );
}
