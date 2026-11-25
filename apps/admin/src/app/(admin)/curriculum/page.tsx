import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function CurriculumPage() {
  await requireAccess("/curriculum");
  return <PageStub title="Curriculum" spec="A-40" />;
}
