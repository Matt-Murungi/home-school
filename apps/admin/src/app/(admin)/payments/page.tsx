import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function PaymentsPage() {
  await requireAccess("/payments");
  return <PageStub title="Payments" spec="A-20" />;
}
