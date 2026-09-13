"use client";

import type { Escala as EscalaT } from "@/lib/indicadores";

/**
 * Escala graduada — cinco degraus da rampa do indicador, com as marcas embaixo.
 *
 * As marcas são `escala.ticks()`: mínimo, os quatro cortes e o máximo. Elas são
 * o que dá sentido à cor do pino — sem legenda, a rampa não comunica nada, e o
 * §10 do design system exige rótulo junto de toda cor de faixa.
 */
export default function Escala({ esc }: { esc: EscalaT | null }) {
  if (!esc) {
    return <div className="min-w-[262px] text-[10px] text-ink-faint">sem escala</div>;
  }
  const ticks = esc.ticks();
  return (
    <div className="flex flex-col gap-1.5 min-w-[262px]">
      {/*
        `gap-3` e o `truncate` nao sao enfeite: com `justify-between` sozinho os
        dois textos encostam quando somam mais que a largura, e "Valorizacao
        ÷ inflacao" colava em "quantas vezes o imovel rendeu acima da inflacao".
      */}
      <div className="flex justify-between items-baseline gap-3 text-[11px] text-ink-soft">
        <span className="shrink-0">{esc.ind.rot}</span>
        <span className="text-ink-faint truncate text-right">{esc.ind.sub}</span>
      </div>
      <div className="flex gap-0.5" role="img" aria-label={`Escala de ${esc.ind.rot}`}>
        {esc.ind.rampa.map((c, i) => (
          <span key={i} className="flex-1 h-[11px]" style={{
            background: c,
            borderRadius: i === 0 ? "6px 2px 2px 6px"
                        : i === esc.ind.rampa.length - 1 ? "2px 6px 6px 2px" : "2px",
          }} />
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-ink-faint nums">
        <span>{ticks[0]}</span>
        <span>{ticks[ticks.length - 1]}</span>
      </div>
    </div>
  );
}
