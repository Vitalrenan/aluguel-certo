"use client";

/**
 * Trilha de progresso da calculadora — o `.trilha` do artefato.
 *
 * Círculo numerado por etapa, ligados por um traço que preenche conforme
 * avança. Abaixo de 860px o rótulo some e ficam só os círculos.
 */
export default function Trilha({ etapas, atual }: {
  etapas: string[];
  atual: number;
}) {
  return (
    <div className="flex items-center mb-6">
      {etapas.map((rot, i) => (
        <div key={rot} className="flex items-center flex-1 last:flex-none">
          <div className={`flex items-center gap-2.5 text-[12.5px] whitespace-nowrap ${
            i === atual ? "text-ink" : "text-ink-faint"
          }`}>
            <b className={`w-6 h-6 rounded-full grid place-items-center text-[11px] font-bold transition-colors ${
              i === atual ? "bg-brand text-white"
              : i < atual ? "bg-brand-soft text-brand"
              : "bg-surface-mute text-ink-faint"
            }`}>
              {i < atual ? "✓" : i + 1}
            </b>
            <span className="max-[860px]:hidden">{rot}</span>
          </div>
          {i < etapas.length - 1 && (
            <div className={`flex-1 h-0.5 mx-3 rounded min-w-3 ${
              i < atual ? "bg-brand-soft" : "bg-[var(--color-linha)]"
            }`} />
          )}
        </div>
      ))}
    </div>
  );
}
