import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function FamiliesPage() {
  await requireAccess("/families");
  return <PageStub title="Families" spec="A-08" />;
}
