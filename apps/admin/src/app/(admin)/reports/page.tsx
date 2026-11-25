import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function ReportsPage() {
  await requireAccess("/reports");
  return <PageStub title="Reports" spec="A-52" />;
}
