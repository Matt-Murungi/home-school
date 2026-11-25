export function ReasonField({
  error,
  defaultValue,
  onChange,
}: {
  error?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
}) {
  return (
    <div className="field">
      <label htmlFor="reason">Reason for change</label>
      <textarea
        id="reason"
        name="reason"
        required
        minLength={10}
        rows={3}
        defaultValue={defaultValue}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
        aria-invalid={!!error}
      />
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}
