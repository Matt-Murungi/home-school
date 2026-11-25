import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function QuestionbankPage() {
  await requireAccess("/question-bank");
  return <PageStub title="Question bank" spec="A-41" />;
}
