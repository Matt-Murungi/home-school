import type { ReactNode } from "react";
import type { EnvLabel } from "@/lib/config";
import { Sidebar } from "./Sidebar";
import { TopBar, type ShellAdmin } from "./TopBar";

export function AdminShell({ envLabel, admin, children }: { envLabel: EnvLabel; admin: ShellAdmin; children: ReactNode }) {
  return (
    <div className="shell">
      <Sidebar roles={admin.roles} />
      <div className="main">
        <TopBar envLabel={envLabel} admin={admin} />
        <main>{children}</main>
      </div>
    </div>
  );
}
