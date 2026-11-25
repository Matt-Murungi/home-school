import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function SessionsPage() {
  await requireAccess("/sessions");
  return <PageStub title="Sessions" spec="A-13" />;
}
