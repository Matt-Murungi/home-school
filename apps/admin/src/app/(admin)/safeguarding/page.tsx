import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function SafeguardingPage() {
  await requireAccess("/safeguarding");
  return <PageStub title="Safeguarding" spec="A-30" />;
}
