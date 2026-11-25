import { PageStub } from "@/components/PageStub";
import { requireAccess } from "@/lib/session";

export default async function OverviewPage() {
  await requireAccess("/");
  return <PageStub title="Overview" spec="A-03" />;
}
