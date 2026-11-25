import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { AdminShell } from "@/components/AdminShell";
import { loadConfig } from "@/lib/config";
import { canAccessPath } from "@/lib/roles";
import { currentAdmin, requestPathname } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function AdminLayout({ children }: { children: ReactNode }) {
  const admin = await currentAdmin();
  if (!admin) redirect("/api/session/clear");
  const pathname = await requestPathname();
  if (pathname !== "/forbidden" && !canAccessPath(pathname, admin.roles)) redirect("/forbidden");
  return (
    <AdminShell envLabel={loadConfig().envLabel} admin={admin}>
      {children}
    </AdminShell>
  );
}
