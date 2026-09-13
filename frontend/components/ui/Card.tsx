import type { ReactNode } from "react";

/**
 * `<Card>` — design-system.md §5.
 *
 * Separação por SOMBRA DIFUSA, nunca por borda (§1, decisão 1). Raio uniforme
 * `--radius-card` — nada de raio misto na mesma tela (§1, decisão 2).
 *
 * Server Component de propósito: **não marcar `"use client"`**. O `Card.tsx`
 * anterior estava marcado sem usar hook nem evento, e isso arrasta toda a
 * subárvore para o bundle do cliente.
 */
export default function Card({
  titulo, sub, acao, className = "", children,
}: {
  titulo?: string;
  sub?: string;
  acao?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={`bg-surface rounded-[var(--radius-card)] shadow-card transicao-card p-6 ${className}`}
    >
      {(titulo || acao) && (
        <header className="flex items-start justify-between gap-4 mb-5">
          <div>
            {titulo && (
              <h2 className="text-lg font-medium text-ink">{titulo}</h2>
            )}
            {sub && <p className="text-sm text-ink-soft mt-0.5">{sub}</p>}
          </div>
          {acao}
        </header>
      )}
      {children}
    </section>
  );
}
