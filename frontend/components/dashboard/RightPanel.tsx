"use client";

import React from "react";
import Card from "@/components/ui/Card";

import { useBairros, useMercado } from "@/hooks/useDashboardData";

/**
 * Painel de mercado.
 *
 * A versão anterior tinha duas séries inventadas ("Evolução de Buscas", SP vs
 * Santos, sete semanas de números fictícios) e uma lista de "Bairros Populares"
 * com Vila Mariana / 760 / R$ 8.5k -- nada disso existia em lugar nenhum.
 *
 * O gráfico de buscas SAIU. Não há coleta longitudinal e não há telemetria de
 * uso; um gráfico de tendência exige série no tempo, e a que temos é a do
 * FipeZAP, que é de preço, não de busca. Substituir uma invenção por outra
 * seria pior que remover.
 *
 * O que entra no lugar: rentabilidade e preço médio por cidade, do índice
 * FipeZAP, com mês de referência visível -- indicador de terceiro sem data na
 * tela envelhece em silêncio.
 */

const brl = (v: number, casas = 0) =>
  v.toLocaleString("pt-BR", { style: "currency", currency: "BRL",
                              minimumFractionDigits: casas,
                              maximumFractionDigits: casas });

const CIDADES_DA_BASE = ["Santos", "São Paulo", "Praia Grande"];

export default function RightPanel() {
  const { mercado, error: erroMercado } = useMercado();
  const { bairros } = useBairros(undefined, false);

  const topo = React.useMemo(() => {
    if (!bairros) return [];
    return [...bairros.bairros]
      .filter((b) => b.preco_m2_mediano != null)
      .sort((a, b) => b.imoveis - a.imoveis)
      .slice(0, 8);
  }, [bairros]);

  return (
    <div className="w-[320px] h-full border-l border-black/5 flex flex-col p-8 overflow-y-auto hide-scrollbar">
      <Card titulo="Mercado por cidade" className="mb-8">
        <div className="text-xs font-semibold text-ink-faint uppercase tracking-wider mb-3">
          {mercado ? `Índice FipeZAP · ${mercado.referencia}` : "Índice FipeZAP"}
        </div>

        {erroMercado && (
          <div className="text-xs text-ink-soft">
            Indicadores indisponíveis: {erroMercado.message}
          </div>
        )}

        {mercado && (
          <div className="flex flex-col gap-3">
            {CIDADES_DA_BASE.map((c) => {
              const d = mercado.cidades[c];
              if (!d) {
                // Cidade da nossa base que a FipeZAP não publica. Aparece
                // dizendo isso, em vez de sumir da lista.
                return (
                  <div key={c} className="flex items-center justify-between">
                    <span className="text-sm text-ink-soft font-medium">{c}</span>
                    <span className="text-[11px] text-ink-faint">sem índice</span>
                  </div>
                );
              }
              return (
                <div key={c} className="flex flex-col gap-1 border-b border-black/5 pb-2 last:border-0">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-ink font-medium">{c}</span>
                    <span className="text-sm font-bold text-brand">
                      {d.yield_anual != null
                        ? `${(d.yield_anual * 100).toFixed(2)}%`
                        : "sem índice"}
                      {d.yield_anual != null && (
                        <span className="text-[10px] font-normal text-ink-faint"> a.a.</span>
                      )}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-ink-faint">
                    <span>venda {d.venda_m2 ? brl(d.venda_m2) : "—"}/m²</span>
                    <span>aluguel {d.locacao_m2 ? brl(d.locacao_m2, 2) : "—"}/m²</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {mercado && mercado.cidades_sem_rentabilidade.length > 0 && (
          <div className="text-[10px] text-ink-faint mt-3 leading-relaxed">
            {mercado.cidades_sem_rentabilidade.length} cidades da planilha não
            publicam rentabilidade e ficam sem estimativa de aluguel.
          </div>
        )}
      </Card>

      <Card titulo="Bairros com mais imóveis" className="flex-1 mb-8">
        <div className="text-xs font-semibold text-ink-faint uppercase tracking-wider mb-3">
          Nossa base · R$/m² mediano
        </div>
        <div className="flex flex-col gap-3">
          {topo.length === 0 && (
            <div className="text-xs text-ink-faint">carregando…</div>
          )}
          {topo.map((b) => (
            <div key={`${b.cidade}|${b.nome}`} className="flex items-center justify-between group">
              <span className="text-sm text-ink-soft font-medium w-24 group-hover:text-brand transition-colors truncate"
                    title={`${b.nome} — ${b.cidade}`}>
                {b.nome}
              </span>
              <span className="text-xs text-ink-faint w-10 text-right">{b.imoveis}</span>
              <span className="text-sm font-semibold text-ink w-20 text-right">
                {b.preco_m2_mediano ? brl(b.preco_m2_mediano) : "—"}
              </span>
            </div>
          ))}
        </div>
        {bairros && (
          <div className="text-[10px] text-ink-faint mt-4 leading-relaxed border-t border-black/5 pt-3">
            {bairros.resumo.n_bairros} bairros com 3 ou mais imóveis.
            {" "}{bairros.resumo.n_bairros - bairros.resumo.n_bairros_com_coordenada}
            {" "}sem coordenada, fora do mapa.
          </div>
        )}
      </Card>
    </div>
  );
}
