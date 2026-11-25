import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function MatchingPage() {
  await requireAccess("/matching");
  return <PageStub title="Matching" spec="A-10" />;
}
