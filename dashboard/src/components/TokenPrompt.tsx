import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useApi } from "../apiContext";

/** Modal asking for the engine's access token. Rendered by App while a request is unauthorized. */
export default function TokenPrompt() {
  const { setToken } = useApi();
  const [value, setValue] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const helpId = useId();
  const inputId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      // jsdom and very old browsers lack showModal: fall back to a plain open dialog.
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    inputRef.current?.focus();
  }, []);

  const trimmed = value.trim();

  function submit(e: FormEvent) {
    e.preventDefault();
    if (trimmed) setToken(trimmed);
  }

  return (
    <dialog
      ref={dialogRef}
      className="dialog"
      aria-labelledby={titleId}
      aria-describedby={helpId}
      // The page cannot work without a token, so Esc does not dismiss it.
      onCancel={(e) => e.preventDefault()}
    >
      <form className="stack" onSubmit={submit}>
        <h2 className="dialog-title" id={titleId}>
          Access token required
        </h2>
        <div className="field">
          <label className="field-label" htmlFor={inputId}>
            Access token
          </label>
          <input
            ref={inputRef}
            id={inputId}
            className="input"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <p className="field-hint" id={helpId}>
            Find it as SKYEYE_API_TOKEN in the engine's .env file
          </p>
        </div>
        <div className="dialog-actions">
          <button className="button button--primary" type="submit" disabled={!trimmed}>
            Save
          </button>
        </div>
      </form>
    </dialog>
  );
}
