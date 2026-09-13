"use client";

import type { DefIndicador } from "@/lib/indicadores";

/**
 * Gráfico de barras do trio — portado VERBATIM do artefato `4027f9a0`.
 *
 * Geometria, tamanhos de fonte e posições são os do original. A versão
 * anterior deste arquivo acrescentou uma hachura para marcar valor
 * ilustrativo, e foi um erro: ela quebrava a leitura do gráfico e não existe
 * na referência. A marca de exemplo fica no CABEÇALHO do card, onde já estava
 * previsto o `<SeloMock>`, e não dentro do desenho.
 *
 * A cor de cada barra vem de `escala.cor(v)`, e a escala é calculada sobre
 * TODAS as cidades — não sobre as cinco do topo. É o que faz as cinco maiores
 * caírem juntas na ponta escura da rampa, em vez de varrerem o arco-íris.
 */

const LARG = 300, ALTO = 172, BASE = 132, TOPO = 26;

export interface Barra {
  valor: number;
  cidade: string;
  uf: string;
  cor: string;
}

export default function GraficoBarras({ itens, media, ind }: {
  itens: Barra[];
  media: number;
  ind: DefIndicador;
}) {
  if (!itens.length) {
    return (
      <div className="h-[172px] grid place-items-center text-xs text-ink-faint">
        sem dado para este indicador
      </div>
    );
  }

  const mx = Math.max(...itens.map((i) => i.valor), media) * 1.12;
  const y = (v: number) => BASE - (v / mx) * (BASE - TOPO);
  const vao = LARG / itens.length;
  const lg = Math.min(38, vao * 0.54);
  const ym = y(media);

  return (
    <svg viewBox={`0 0 ${LARG} ${ALTO}`} width="100%" height={ALTO}
         className="overflow-visible mt-0.5" role="img"
         aria-label={`Top ${itens.length} por ${ind.rot}`}>
      <line x1="0" y1={BASE} x2={LARG} y2={BASE}
            stroke="var(--color-linha)" strokeWidth="1" />

      {itens.map((it) => {
        const k = itens.indexOf(it);
        const cx = vao * (k + 0.5);
        const yb = y(it.valor);
        const h = Math.max(2, BASE - yb);
        return (
          <g key={`${it.cidade}|${it.uf}`} className="[&:hover_rect]:opacity-[0.88]">
            <rect x={+(cx - lg / 2).toFixed(1)} y={+yb.toFixed(1)}
                  width={lg} height={+h.toFixed(1)} rx="5" fill={it.cor} />
            <text x={+cx.toFixed(1)} y={+(yb - 6).toFixed(1)} textAnchor="middle"
                  fontSize="10.5" fontWeight="600" fill="var(--color-ink)">
              {ind.curto(it.valor)}
            </text>
            <text x={+cx.toFixed(1)} y={BASE + 15} textAnchor="middle"
                  fontSize="9.5" fill="var(--color-ink-soft)">
              {it.cidade.split(" ")[0]}
            </text>
            <text x={+cx.toFixed(1)} y={BASE + 26} textAnchor="middle"
                  fontSize="9.5" fill="var(--color-ink-soft)">
              {it.uf}
            </text>
          </g>
        );
      })}

      <line x1="0" y1={+ym.toFixed(1)} x2={LARG} y2={+ym.toFixed(1)}
            stroke="var(--color-ink-faint)" strokeWidth="1" strokeDasharray="4 3" />
      <rect x={LARG - 92} y={+(ym - 9).toFixed(1)} width="92" height="17" rx="8"
            fill="var(--color-surface)" stroke="var(--color-linha)" strokeWidth="1" />
      <text x={LARG - 46} y={+(ym + 3.5).toFixed(1)} textAnchor="middle"
            fontSize="9.5" fill="var(--color-ink-soft)">
        média {ind.curto(media)}
      </text>
    </svg>
  );
}
