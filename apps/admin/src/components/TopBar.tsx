import { ROLE_NAMES, type AdminRole } from "@/lib/roles";
import type { EnvLabel } from "@/lib/config";
import { ENTITY_LABEL, searchableTypes } from "@/lib/search/policy";
import { EnvBadge } from "./EnvBadge";
import { SearchBox } from "./SearchBox";

export type ShellAdmin = { name: string; email: string; roles: AdminRole[] };

export function TopBar({ envLabel, admin }: { envLabel: EnvLabel; admin: ShellAdmin }) {
  const searchGroups = [...searchableTypes(admin.roles).keys()].map((t) => ENTITY_LABEL[t]);
  return (
    <header className="topbar">
      <SearchBox groups={searchGroups} />
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
