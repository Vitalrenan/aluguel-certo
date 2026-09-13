"use client";

import React from "react";
import { AlertTriangle, Info } from "lucide-react";

import type { Estimativa } from "@/lib/api";

/**
 * O resultado da estimativa.
 *
 * TRÊS REGRAS DE PRODUTO, todas vindas de decisão registrada, e todas visíveis
 * neste arquivo:
 *
 * 1. **O ponto nunca aparece sozinho.** A faixa vem junto, sempre. Um número
 *    isolado em fonte grande comunica uma precisão que a medição não sustenta.
 *
 * 2. **A faixa NÃO é rotulada "80% de confiança".** Os quantis são 10/90, o
 *    que seria 80% nominal; a cobertura medida no holdout é 72,9%. O que se
 *    exibe é o número medido, e ele vem da própria resposta.
 *
 * 3. **O aluguel é marcado como âncora, não como previsão.** Ele é o preço
 *    estimado multiplicado pela rentabilidade da cidade (FipeZAP). A resposta
 *    traz `tipo: "ancora_de_mercado"` justamente para a tela não poder dizer
 *    "o modelo calculou". E a faixa dele se chama `envelope`, não `intervalo`,
 *    porque compõe duas incertezas e não tem cobertura medida.
 */

const brl = (v: number, casas = 0) =>
  v.toLocaleString("pt-BR", { style: "currency", currency: "BRL",
                              minimumFractionDigits: casas,
                              maximumFractionDigits: casas });
const pct = (v: number, casas = 1) =>
  `${(v * 100).toLocaleString("pt-BR", { minimumFractionDigits: casas,
                                         maximumFractionDigits: casas })}%`;

function Faixa({ min, max, ponto }: { min: number; max: number; ponto: number }) {
  const largura = max - min;
  const posicao = largura > 0 ? ((ponto - min) / largura) * 100 : 50;
  return (
    <div className="mt-4">
      <div className="relative h-2 rounded-full bg-gradient-to-r from-brand/20 via-brand/50 to-brand/20">
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-brand ring-2 ring-white shadow"
          style={{ left: `${Math.max(0, Math.min(100, posicao))}%` }}
        />
      </div>
      <div className="flex justify-between text-xs text-ink-soft mt-2">
        <span>{brl(min)}</span>
        <span>{brl(max)}</span>
      </div>
    </div>
  );
}

export default function Resultado({ r }: { r: Estimativa }) {
  const cobertura = r.modelo.cobertura_intervalo;

  return (
    <div className="flex flex-col gap-6">
      {/* Preço de venda */}
      <div className="bg-surface rounded-[var(--radius-card)] border border-black/5 shadow-card p-8">
        <div className="text-sm font-medium text-ink-faint uppercase tracking-wider mb-2">
          Valor estimado de venda
        </div>
        <div className="text-5xl font-bold text-ink tracking-tight">
          {brl(r.estimativa)}
        </div>
        <Faixa min={r.intervalo.min} max={r.intervalo.max} ponto={r.estimativa} />
        <div className="text-xs text-ink-soft mt-3 leading-relaxed">
          {cobertura != null ? (
            <>
              Esta faixa contém o preço real em <strong>{pct(cobertura)}</strong>{" "}
              dos casos, medido no holdout do modelo. Não é 80%.
            </>
          ) : (
            <>A faixa não tem cobertura medida para este modelo.</>
          )}
        </div>
      </div>

      {/* Aluguel — âncora, não previsão */}
      {r.aluguel ? (
        <div className="bg-surface rounded-[var(--radius-card)] border border-black/5 shadow-card p-8">
          <div className="flex items-start justify-between gap-4 mb-2">
            <div className="text-sm font-medium text-ink-faint uppercase tracking-wider">
              Aluguel de referência
            </div>
            <span className="text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-700 px-2 py-1 rounded-full whitespace-nowrap">
              âncora de mercado
            </span>
          </div>
          <div className="text-4xl font-bold text-ink tracking-tight">
            {brl(r.aluguel.estimativa)}
            <span className="text-base font-normal text-ink-faint"> /mês</span>
          </div>
          <Faixa
            min={r.aluguel.envelope.min}
            max={r.aluguel.envelope.max}
            ponto={r.aluguel.estimativa}
          />
          <div className="text-xs text-ink-soft mt-3 leading-relaxed">
            <strong>Não é previsão de um modelo de aluguel.</strong> É o valor
            de venda estimado multiplicado pela rentabilidade de{" "}
            {r.aluguel.yield.cidade}:{" "}
            <strong>{pct(r.aluguel.yield.mensal, 3)} ao mês</strong>{" "}
            ({r.aluguel.yield.referencia}).
          </div>
          <div className="text-xs text-ink-faint mt-2 leading-relaxed">
            A faixa é {r.aluguel.largura_do_envelope.toLocaleString("pt-BR")}× mais
            larga que o ponto porque soma duas incertezas — a do preço e a da
            própria rentabilidade. Ela <strong>não tem cobertura medida</strong>,
            ao contrário da faixa de venda acima.
          </div>
        </div>
      ) : (
        <div className="bg-surface-mute rounded-[var(--radius-card)] border border-black/5 p-6 flex gap-3">
          <Info className="w-5 h-5 text-ink-faint shrink-0 mt-0.5" />
          <div>
            <div className="text-sm font-semibold text-ink mb-1">
              Sem estimativa de aluguel para esta cidade
            </div>
            <div className="text-xs text-ink-soft leading-relaxed">
              O aluguel só é calculado onde existe rentabilidade publicada com
              fonte declarada. Preferimos não mostrar nada a inventar uma taxa.
            </div>
          </div>
        </div>
      )}

      {/* Ressalvas — o que a medição não sustenta */}
      {r.ressalvas.length > 0 && (
        <div className="bg-amber-50 rounded-[var(--radius-card)] border border-amber-200 p-6">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-900 mb-3">
            <AlertTriangle className="w-4 h-4" />
            O que este número não garante
          </div>
          <ul className="flex flex-col gap-2">
            {r.ressalvas.map((t, i) => (
              <li key={i} className="text-xs text-amber-900/80 leading-relaxed">
                • {t}
              </li>
            ))}
          </ul>
        </div>
      )}

      {r.campos_ausentes.length > 0 && (
        <div className="text-xs text-ink-soft leading-relaxed">
          Não informado: <strong>{r.campos_ausentes.map((c) => c.replace(/_/g, "-")).join(", ")}</strong>.
          {(r.campos_ausentes.includes("condominio") ||
            r.campos_ausentes.includes("iptu")) && (
            <> Informar condomínio e IPTU reduz o erro do modelo em cerca de
            R$ 6 mil — é o segundo maior ganho do formulário.</>
          )}
        </div>
      )}

      {/* Proveniência */}
      <div className="text-[11px] text-ink-faint leading-relaxed border-t border-black/5 pt-4">
        modelo <span className="font-mono">{r.modelo.versao}</span> ·{" "}
        base {r.modelo.base} · {r.modelo.n_treino.toLocaleString("pt-BR")} imóveis
        de treino · segmento {r.modelo.segmento}
        {r.modelo.erro_mediano_holdout != null && (
          <> · erro mediano {pct(r.modelo.erro_mediano_holdout)}</>
        )}
      </div>
    </div>
  );
}
