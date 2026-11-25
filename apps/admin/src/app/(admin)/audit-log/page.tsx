import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function AuditlogPage() {
  await requireAccess("/audit-log");
  return <PageStub title="Audit log" spec="A-53" />;
}
