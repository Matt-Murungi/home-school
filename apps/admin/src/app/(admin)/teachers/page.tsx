import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function TeachersPage() {
  await requireAccess("/teachers");
  return <PageStub title="Teachers" spec="A-06" />;
}
