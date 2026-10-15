"use client";

// The library's signature-mode filter submits its form on change (static/studio.js does this
// for [data-autosubmit] today). Without JavaScript the form still works with Enter.
export function AutoSubmitSelect(props: {
  name: string;
  value: string;
  label: string;
  options: [string, string][];
}) {
  return (
    <select
      className="select"
      name={props.name}
      defaultValue={props.value}
      aria-label={props.label}
      onChange={(event) => event.currentTarget.form?.requestSubmit()}
    >
      {props.options.map(([key, text]) => (
        <option key={key} value={key}>
          {text}
        </option>
      ))}
    </select>
  );
}
