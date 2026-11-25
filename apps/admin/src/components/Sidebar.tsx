"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { navFor } from "@/lib/nav";
import type { AdminRole } from "@/lib/roles";

export function Sidebar({ roles }: { roles: AdminRole[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="sidebar">
      <div className="brand">Home School</div>
      <ul>
        {navFor(roles).map((n) => {
          const active =
            n.href === "/"
              ? pathname === "/"
              : pathname === n.href || pathname.startsWith(n.href + "/");
          return (
            <li key={n.href}>
              <Link href={n.href} aria-current={active ? "page" : undefined}>
                {n.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
