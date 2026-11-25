import type { AdminRole } from "./roles";

export type NavItem = { label: string; href: string; roles: readonly AdminRole[]; spec: string };

const ALL: readonly AdminRole[] = ["opsadmin", "curator", "safeguardinglead", "superadmin"];
const OPS: readonly AdminRole[] = ["opsadmin", "superadmin"];

// Order and names: Design Brief "Admin dashboard - left sidebar". Role visibility is an
// assumption to confirm with the owner (SR 6.1 does not tabulate screens per role).
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Overview", href: "/", roles: ALL, spec: "A-03" },
  { label: "Queues", href: "/queues", roles: ["opsadmin", "safeguardinglead", "superadmin"], spec: "A-02" },
  { label: "Teachers", href: "/teachers", roles: ["opsadmin", "safeguardinglead", "superadmin"], spec: "A-06" },
  { label: "Families", href: "/families", roles: ["opsadmin", "safeguardinglead", "superadmin"], spec: "A-08" },
  { label: "Matching", href: "/matching", roles: OPS, spec: "A-10" },
  { label: "Sessions", href: "/sessions", roles: ["opsadmin", "safeguardinglead", "superadmin"], spec: "A-13" },
  { label: "Payments", href: "/payments", roles: OPS, spec: "A-20" },
  { label: "Payouts", href: "/payouts", roles: OPS, spec: "A-21" },
  { label: "Safeguarding", href: "/safeguarding", roles: ["safeguardinglead", "superadmin"], spec: "A-30" },
  { label: "Curriculum", href: "/curriculum", roles: ["curator", "superadmin"], spec: "A-40" },
  { label: "Question bank", href: "/question-bank", roles: ["curator", "superadmin"], spec: "A-41" },
  { label: "Reports", href: "/reports", roles: OPS, spec: "A-52" },
  { label: "Settings", href: "/settings", roles: ["superadmin"], spec: "A-50" },
  { label: "Audit log", href: "/audit-log", roles: ["safeguardinglead", "superadmin"], spec: "A-53" },
];

export function navFor(roles: readonly AdminRole[]): NavItem[] {
  return NAV_ITEMS.filter((n) => n.roles.some((r) => roles.includes(r)));
}
