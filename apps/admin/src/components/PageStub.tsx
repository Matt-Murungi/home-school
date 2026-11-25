export function PageStub({ title, spec }: { title: string; spec: string }) {
  return (
    <section>
      <h1>{title}</h1>
      <p>Screen {spec} is built in a later ticket. This page is an empty placeholder.</p>
    </section>
  );
}
