import type { ReactNode } from "react";

/**
 * `<CardAcento>` — o menta. design-system.md §5.
 *
 * **UM POR TELA.** É o único bloco de cor cheia, e é o que dá o ponto de
 * respiro na referência. Dois cards menta e o efeito acaba (§1, decisão 6).
 *
 * O `StatPanel` que eu escrevi em 2026-09-09 tinha DOIS cards de acento lado a
 * lado, índigo e âmbar. É a violação que mais pesou no "ficou feio": a
 * referência é branca e silenciosa, com cor em quatro lugares apenas.
 */
export default function CardAcento({
  numero, rotulo, nota, rodape, className = "",
}: {
  numero: ReactNode;
  rotulo: string;
  nota?: string;
  rodape?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`bg-mint text-mint-ink rounded-[var(--radius-card)] shadow-card p-6 flex flex-col gap-1 ${className}`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] opacity-70">
        {rotulo}
      </p>
      <div className="text-5xl font-semibold tracking-tight nums">{numero}</div>
      {nota && <p className="text-sm opacity-80 leading-relaxed mt-1">{nota}</p>}
      {rodape && <div className="mt-4 opacity-70">{rodape}</div>}
    </section>
  );
}
