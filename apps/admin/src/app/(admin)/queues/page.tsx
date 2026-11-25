import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function QueuesPage() {
  await requireAccess("/queues");
  return <PageStub title="Queues" spec="A-02" />;
}
