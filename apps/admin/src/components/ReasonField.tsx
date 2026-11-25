export function ReasonField({ error, defaultValue }: { error?: string; defaultValue?: string }) {
  return (
    <div className="field">
      <label htmlFor="reason">Reason for change</label>
      <textarea id="reason" name="reason" required minLength={10} rows={3} defaultValue={defaultValue} aria-invalid={!!error} />
      {error && <p role="alert" className="field-error">{error}</p>}
    </div>
  );
}
