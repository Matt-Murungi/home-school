import type { AdminRole } from "./roles";

export type NavItem = { label: string; href: string; roles: readonly AdminRole[]; spec: string };

const ALL: readonly AdminRole[] = ["opsadmin", "curator", "safeguardinglead", "superadmin"];
// SR 6.2: ops read the catalogue and questions; curators read/write.
const CATALOGUE: readonly AdminRole[] = ["opsadmin", "curator", "superadmin"];
const OPS: readonly AdminRole[] = ["opsadmin", "superadmin"];

// Order and names: Design Brief "Admin dashboard - left sidebar". Role visibility follows SR 6.2
// where it speaks (families, teachers, sessions, payments, catalogue, safeguarding, audit log).
// UNCONFIRMED guesses, to confirm with the owner: Overview (all), Queues, Matching, Payouts,
// Reports (ops), Settings (superadmin only). A-51 Admin users and roles has no sidebar item yet.
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Overview", href: "/", roles: ALL, spec: "A-03" },
  {
    label: "Queues",
    href: "/queues",
    roles: ["opsadmin", "safeguardinglead", "superadmin"],
    spec: "A-02",
  },
  {
    label: "Teachers",
    href: "/teachers",
    roles: ["opsadmin", "safeguardinglead", "superadmin"],
    spec: "A-06",
  },
  {
    label: "Families",
    href: "/families",
    roles: ["opsadmin", "safeguardinglead", "superadmin"],
    spec: "A-08",
  },
  { label: "Matching", href: "/matching", roles: OPS, spec: "A-10" },
  {
    label: "Sessions",
    href: "/sessions",
    roles: ["opsadmin", "safeguardinglead", "superadmin"],
    spec: "A-13",
  },
  { label: "Payments", href: "/payments", roles: OPS, spec: "A-20" },
  { label: "Payouts", href: "/payouts", roles: OPS, spec: "A-21" },
  {
    label: "Safeguarding",
    href: "/safeguarding",
    roles: ["safeguardinglead", "superadmin"],
    spec: "A-30",
  },
  { label: "Curriculum", href: "/curriculum", roles: CATALOGUE, spec: "A-40" },
  {
    label: "Question bank",
    href: "/question-bank",
    roles: CATALOGUE,
    spec: "A-41",
  },
  { label: "Reports", href: "/reports", roles: OPS, spec: "A-52" },
  { label: "Settings", href: "/settings", roles: ["superadmin"], spec: "A-50" },
  {
    label: "Audit log",
    href: "/audit-log",
    roles: ["opsadmin", "safeguardinglead", "superadmin"],
    spec: "A-53",
  },
];

export function navFor(roles: readonly AdminRole[]): NavItem[] {
  return NAV_ITEMS.filter((n) => n.roles.some((r) => roles.includes(r)));
}
