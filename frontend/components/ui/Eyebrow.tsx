import type { ReactNode } from "react";

/** `<Eyebrow>` — design-system.md §3. "MENU", "BAIRRO", rótulo de seção. */
export default function Eyebrow({ children, className = "" }: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p className={`text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint ${className}`}>
      {children}
    </p>
  );
}
