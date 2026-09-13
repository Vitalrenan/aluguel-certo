"use client";
import React from 'react';
import { CheckCircle2, ShieldAlert, TrendingDown, TrendingUp } from 'lucide-react';

import { useBairros, useModelo } from '@/hooks/useDashboardData';

/**
 * Estatísticas do modelo servido.
 *
 * TUDO AQUI VEM DE `GET /modelo` E `GET /bairros/indicadores`. A versão
 * anterior tinha os números escritos no JSX -- 11.344 imóveis, erro mediano
 * 9,8%, RMSE 97k e 85k -- e os quatro estavam errados: o 9,8% é anterior à
 * correção do cenário (D-034), o ganho de imputação foi invalidado pela mesma
 * correção, e a base tem 12.097 imóveis distintos, não 11.344. Número fixo no
 * JSX envelhece sem ninguém perceber, e este envelheceu passando por "medido".
 *
 * Duas regras de exibição que vêm de decisão registrada:
 *
 *   - o rótulo "confiança alta/média/baixa" NÃO existe. Foi removido do plano
 *     de UX por não ter origem reproduzível. O que se mostra é a cobertura
 *     medida do intervalo, e ela é 72,9% -- não os 80% nominais;
 *   - o viés aparece com SINAL e nas duas unidades. Em reais o modelo
 *     subestima; em percentual superestima. Os dois são corretos e opostos, e
 *     mostrar um só passa a impressão errada.
 */

const brl = (v: number, casas = 0) =>
  v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL',
                              minimumFractionDigits: casas,
                              maximumFractionDigits: casas });
const pct = (v: number, casas = 1) =>
  `${(v * 100).toLocaleString('pt-BR', { minimumFractionDigits: casas,
                                         maximumFractionDigits: casas })}%`;

function Carregando() {
  return <div className="animate-pulse h-8 w-32 bg-surface-mute rounded-[var(--radius-control)]" />;
}

function Falha({ mensagem }: { mensagem: string }) {
  return (
    <div className="bg-res-atencao/10 border border-res-atencao/30 p-4 rounded-[var(--radius-control)]">
      <div className="text-sm font-semibold text-ink mb-1">
        Modelo indisponível
      </div>
      <div className="text-xs text-ink-soft">{mensagem}</div>
    </div>
  );
}

export default function StatPanel() {
  const { modelo, error, isLoading } = useModelo();
  const { bairros } = useBairros();

  const decisao = modelo?.holdout?.sem_opcionais;
  const completo = modelo?.holdout?.completo;

  return (
    <div className="w-[380px] bg-surface/70 backdrop-blur-xl h-full z-10 border-r border-black/5 flex flex-col p-8 shadow-card relative overflow-y-auto hide-scrollbar">
      <h1 className="text-2xl font-medium text-ink mb-1">Estatísticas do Modelo</h1>
      <div className="flex items-center text-xs font-semibold text-ink-faint mb-6 uppercase tracking-wider gap-2">
        <span>{modelo ? `${modelo.base} · ${modelo.segmento}` : 'Base de treinamento'}</span>
      </div>

      {error && <Falha mensagem={error.message} />}

      {!error && (
        <>
          {/* Imóveis na base — da tabela de bairros, não do modelo */}
          <div className="text-5xl font-bold text-ink mb-1 tracking-tight">
            {isLoading || !bairros ? <Carregando />
              : bairros.resumo.n_imoveis.toLocaleString('pt-BR')}
          </div>
          <div className="text-xs text-ink-faint mb-8">
            imóveis no recorte{bairros ? ` · ${bairros.resumo.n_bairros} bairros em ${bairros.resumo.cidades.length} cidades` : ''}
          </div>

          {/* Erro mediano nos dois cenários */}
          <div className="grid grid-cols-2 gap-4 mb-8">
            <div className="bg-brand/10 p-4 rounded-[var(--radius-card)] flex flex-col gap-3">
              <div className="w-10 h-10 rounded-[var(--radius-control)] bg-brand flex items-center justify-center ">
                <CheckCircle2 className="text-white w-5 h-5" />
              </div>
              <div>
                <div className="text-ink-soft text-sm font-medium mb-1">Erro Mediano</div>
                <div className="text-ink font-bold text-2xl">
                  {completo ? pct(completo.erro_mediano) : <Carregando />}
                </div>
                <div className="text-[10px] text-brand font-semibold uppercase tracking-wider mt-1">
                  Com condomínio e IPTU
                </div>
              </div>
            </div>
            <div className="bg-res-atencao/10 p-4 rounded-[var(--radius-card)] flex flex-col gap-3">
              <div className="w-10 h-10 rounded-[var(--radius-control)] bg-res-atencao flex items-center justify-center ">
                <ShieldAlert className="text-white w-5 h-5" />
              </div>
              <div>
                <div className="text-ink-soft text-sm font-medium mb-1">Erro Mediano</div>
                <div className="text-ink font-bold text-2xl">
                  {decisao ? pct(decisao.erro_mediano) : <Carregando />}
                </div>
                <div className="text-[10px] text-res-atencao font-semibold uppercase tracking-wider mt-1">
                  Sem informá-los
                </div>
              </div>
            </div>
          </div>

          {/* RMSE nos dois cenários */}
          <div className="flex flex-col gap-4 mb-6">
            <div className="bg-surface p-5 rounded-[var(--radius-card)] shadow-card border border-black/5 flex flex-col gap-1">
              <div className="text-ink-faint text-sm font-medium">RMSE (sem condomínio/IPTU)</div>
              <div className="text-2xl font-bold text-ink">
                {decisao ? brl(decisao.rmse_brl) : <Carregando />}
              </div>
              <div className="text-ink-faint text-[11px] mt-1">
                Cenário de decisão: o usuário típico não informa os dois.
              </div>
            </div>

            <div className="bg-surface p-5 rounded-[var(--radius-card)] shadow-card border border-black/5 flex flex-col gap-1">
              <div className="text-ink-faint text-sm font-medium">RMSE (com condomínio/IPTU)</div>
              <div className="flex items-end gap-3">
                <span className="text-2xl font-bold text-ink">
                  {completo ? brl(completo.rmse_brl) : <Carregando />}
                </span>
                {decisao && completo && (
                  <span className="flex items-center text-res-oportuno text-xs font-bold mb-1">
                    <TrendingDown className="w-3 h-3 mr-1" />
                    {brl(decisao.rmse_brl - completo.rmse_brl)} de ganho
                  </span>
                )}
              </div>
              <div className="text-ink-faint text-[11px] mt-1">
                Informar os dois é o segundo maior ganho do formulário.
              </div>
            </div>
          </div>

          {/* Viés — com sinal, nas duas unidades */}
          {decisao && (
            <div className="bg-surface p-5 rounded-[var(--radius-card)] shadow-card border border-black/5 flex flex-col gap-2 mb-6">
              <div className="text-ink-faint text-sm font-medium flex items-center gap-2">
                {decisao.vies_brl < 0 ? <TrendingDown className="w-4 h-4" />
                                      : <TrendingUp className="w-4 h-4" />}
                Viés (erro médio com sinal)
              </div>
              <div className="flex items-baseline gap-4">
                <div>
                  <div className="text-xl font-bold text-ink">
                    {brl(decisao.vies_brl)}
                  </div>
                  <div className="text-[10px] text-ink-faint uppercase tracking-wider">
                    por imóvel, em reais
                  </div>
                </div>
                <div>
                  <div className="text-xl font-bold text-ink">
                    {pct(decisao.vies_pct)}
                  </div>
                  <div className="text-[10px] text-ink-faint uppercase tracking-wider">
                    relativo
                  </div>
                </div>
              </div>
              <div className="text-ink-faint text-[11px]">
                Os dois sinais são corretos e opostos: em reais o modelo
                subestima, em percentual superestima. É compressão para a média.
              </div>
            </div>
          )}

          {/* Cobertura do intervalo — o número medido, não os 80% nominais */}
          {decisao?.cobertura_intervalo != null && (
            <div className="bg-surface p-5 rounded-[var(--radius-card)] shadow-card border border-black/5 flex flex-col gap-1 mb-6">
              <div className="text-ink-faint text-sm font-medium">A faixa acerta</div>
              <div className="text-2xl font-bold text-ink">
                {pct(decisao.cobertura_intervalo)}
              </div>
              <div className="text-ink-faint text-[11px] mt-1">
                Medido no holdout de {modelo?.n_holdout} imóveis. Os quantis
                são 10/90, nominalmente 80% — a faixa entrega menos que isso.
              </div>
            </div>
          )}

          {modelo && (
            <div className="text-[10px] text-ink-faint leading-relaxed border-t border-black/5 pt-4">
              modelo <span className="font-mono">{modelo.versao}</span> ·
              {' '}{modelo.n_treino.toLocaleString('pt-BR')} imóveis de treino ·
              {' '}dropout {modelo.dropout_opcionais} ·
              {' '}treinado em {new Date(modelo.treinado_em).toLocaleDateString('pt-BR')}
            </div>
          )}
        </>
      )}
    </div>
  );
}
