import type { ReactNode } from "react";

/**
 * `<Pilula>` — design-system.md §5.
 *
 * Duas variantes, e a diferença entre elas não é estética:
 *
 *   - `contagem` — fundo `brand-soft`, texto `brand`. Ex.: "412 imóveis".
 *   - `faixa` — ponto colorido de 6px MAIS o rótulo textual.
 *
 * A variante de faixa **sempre carrega o rótulo**, nunca só a cor. É requisito
 * de acessibilidade do plano §1: cor sozinha não comunica para quem não a
 * distingue, e o checklist do §10 verifica isso.
 */

// Rampa azul de 5 passos. A faixa NAO tem cor semantica -- quem carrega o
// sentido e o rotulo textual ao lado. Ver a nota da paleta em globals.css.
const CORES_DE_FAIXA: Record<string, string> = {
  abaixo: "var(--color-rampa-2)",
  media: "var(--color-rampa-3)",
  acima: "var(--color-rampa-5)",
};

const ROTULOS: Record<string, string> = {
  abaixo: "abaixo da média",
  media: "na média",
  acima: "acima da média",
};

export function PilulaContagem({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center h-[22px] px-2.5 rounded-[var(--radius-pill)] bg-brand-soft text-brand text-xs font-medium nums">
      {children}
    </span>
  );
}

export function PilulaFaixa({ faixa }: { faixa: string | null }) {
  if (!faixa) return null;
  return (
    <span className="inline-flex items-center gap-1.5 h-[22px] px-2.5 rounded-[var(--radius-pill)] bg-surface-mute text-ink-soft text-xs font-medium">
      <span
        aria-hidden
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ background: CORES_DE_FAIXA[faixa] ?? "var(--color-ink-faint)" }}
      />
      {ROTULOS[faixa] ?? faixa}
    </span>
  );
}
