import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function PayoutsPage() {
  await requireAccess("/payouts");
  return <PageStub title="Payouts" spec="A-21" />;
}
