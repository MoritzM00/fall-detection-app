import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

export const BASELINE_SAMPLING = { frames: 16, fps: 7.5, size: 448 };

export function isBaselineSampling(frames: number, fps: number, size: number): boolean {
  return frames === BASELINE_SAMPLING.frames && fps === BASELINE_SAMPLING.fps && size === BASELINE_SAMPLING.size;
}

export function samplingSummary(frames: number, fps: number, size: number): string {
  return `${frames} frames, ${Number(fps.toFixed(2))} fps, ${size} px`;
}

export function settingsSummary(frames: number, fps: number, size: number, chainOfThought: boolean): string {
  return `${samplingSummary(frames, fps, size)}, ${chainOfThought ? "chain of thought" : "zero-shot"}`;
}

type SummaryProps = {
  /** Title of the research preset the settings reproduce exactly, or null when edited. */
  preset: string | null;
  invalid: boolean;
  detail: string;
  disabled: boolean;
  onEdit: () => void;
};

/** One-line view of the active settings; edits happen in the settings dialog. */
export function SettingsSummary({ preset, invalid, detail, disabled, onEdit }: SummaryProps) {
  return <div className="settings-summary">
    <div>
      <strong>{preset ? `Research ${preset.toLowerCase()}` : "Custom settings"}</strong>
      {invalid ? <small className="experimental">Invalid</small> : !preset && <small className="experimental">Experimental</small>}
      <span>{detail}</span>
    </div>
    <button type="button" className="button quiet" disabled={disabled} onClick={onEdit}>Settings</button>
  </div>;
}

type GroupProps = {
  index: string;
  title: string;
  hint: string;
  edited: boolean;
  children: ReactNode;
};

/** One numbered settings section, marked when it departs from the research defaults. */
export function SettingsGroup({ index, title, hint, edited, children }: GroupProps) {
  return <section className="settings-section" aria-label={title}>
    <header className="settings-section-head">
      <span className="settings-index" aria-hidden="true">{index}</span>
      <div><h3>{title}</h3><p>{hint}</p></div>
      <small className={edited ? "section-chip edited" : "section-chip"}>{edited ? "Edited" : "Research default"}</small>
    </header>
    {children}
  </section>;
}

type DialogProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
};

/** Modal side sheet for settings that most runs leave at the baseline. */
export function SettingsDialog({ open, title, onClose, children, footer }: DialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  // The native close event also fires for closes driven by `open`; only report the ones the user started (Escape).
  const expectedOpen = useRef(open);
  expectedOpen.current = open;
  // A drag that starts in the sheet (selecting prompt text) and ends on the backdrop is not a backdrop click.
  const pressedBackdrop = useRef(false);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);
  return <dialog ref={dialog} className="settings-dialog" aria-label={title} onClose={() => { if (expectedOpen.current) onClose(); }}
    onPointerDown={(event) => { pressedBackdrop.current = event.target === event.currentTarget; }}
    onClick={(event) => { if (pressedBackdrop.current && event.target === event.currentTarget) onClose(); }}>
    <header><h2>{title}</h2><button type="button" className="dialog-close" aria-label="Close settings" onClick={onClose}>×</button></header>
    <div className="settings-body">{children}</div>
    <footer>{footer}</footer>
  </dialog>;
}
