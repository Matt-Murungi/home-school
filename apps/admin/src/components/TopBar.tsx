import { ROLE_NAMES, type AdminRole } from "@/lib/roles";
import type { EnvLabel } from "@/lib/config";
import { EnvBadge } from "./EnvBadge";

export type ShellAdmin = { name: string; email: string; roles: AdminRole[] };

export function TopBar({ envLabel, admin }: { envLabel: EnvLabel; admin: ShellAdmin }) {
  return (
    <header className="topbar">
      <input
        type="search"
        role="searchbox"
        aria-label="Search"
        placeholder="Search people, sessions, payments (coming soon)"
        disabled
      />
      <EnvBadge label={envLabel} />
      <div className="who">
        <span>{admin.name || admin.email}</span>
        <small>{admin.roles.map((r) => ROLE_NAMES[r]).join(", ")}</small>
      </div>
      <form action="/api/session" method="post">
        <input type="hidden" name="step" value="logout" />
        <button type="submit">Sign out</button>
      </form>
    </header>
  );
}
