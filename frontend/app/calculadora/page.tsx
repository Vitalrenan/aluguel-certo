"use client";

import React from "react";
import { Loader2 } from "lucide-react";

import Resultado from "@/components/calculadora/Resultado";
import Trilha from "@/components/calculadora/Trilha";
import Card from "@/components/ui/Card";
import Eyebrow from "@/components/ui/Eyebrow";
import { useBairrosDoModelo, useOpcoes } from "@/hooks/useDashboardData";
import { ApiError, estimar, type Estimativa, type Pergunta } from "@/lib/api";

/**
 * Calculadora — visual de quatro etapas do artefato `4027f9a0`, com o
 * formulário REAL do modelo.
 *
 * Duas coisas se combinam aqui e a distinção importa:
 *
 *   - o **desenho** vem do artefato: trilha de progresso, campos em duas
 *     colunas, opções em cartão clicável, bloco de opcionais destacado;
 *   - a **lista de campos** vem de `GET /opcoes`, não do artefato. O artefato
 *     perguntava nove coisas; o modelo consome dezoito. Foi decisão de
 *     2026-09-10: a calculadora tem os campos que a API precisa.
 *
 * As etapas agrupam as perguntas por tipo, e o agrupamento é derivado — não há
 * lista de campos escrita neste arquivo. Pergunta nova no modelo cai sozinha na
 * etapa certa.
 */

const RESIDENCIAIS = new Set([
  "apartamento", "casa", "sobrado", "cobertura", "kitnet", "studio", "flat",
]);

const ETAPAS = ["Onde fica", "O imóvel", "Comodidades", "Valores"] as const;

type Valores = Record<string, string | number | boolean | null | undefined>;

/** Em qual etapa cada pergunta cai, pelo tipo e pela obrigatoriedade. */
function etapaDe(p: Pergunta): number {
  if (p.api === "cidade" || p.api === "bairro") return 0;
  if (p.tipo === "booleana" || p.api === "andar") return 2;
  if (p.api === "condominio" || p.api === "iptu") return 3;
  return 1;
}

function Campo({ p, valor, aoMudar, bairros }: {
  p: Pergunta;
  valor: Valores[string];
  aoMudar: (v: Valores[string]) => void;
  bairros?: string[];
}) {
  const base =
    "w-full px-3.5 py-2.5 border border-[var(--color-linha)] rounded-[var(--radius-control)] text-sm text-ink bg-surface transition-shadow focus:outline-none focus:border-brand focus:ring-[3px] focus:ring-brand-soft";

  const rotulo = (
    <label className="text-[12.5px] font-semibold text-ink-soft">
      {p.rotulo}
      {!p.obrigatoria && (
        <i className="not-italic font-normal text-ink-faint ml-1.5">opcional</i>
      )}
    </label>
  );

  if (p.api === "bairro") {
    return (
      <div className="flex flex-col gap-[7px] min-w-0">
        {rotulo}
        <select className={base} value={(valor as string) ?? ""}
                onChange={(e) => aoMudar(e.target.value || null)}
                disabled={!bairros?.length}>
          <option value="">{bairros?.length ? "selecione" : "escolha a cidade primeiro"}</option>
          {bairros?.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
    );
  }

  if (p.tipo === "categorica" && p.opcoes?.length) {
    return (
      <div className="flex flex-col gap-[7px] min-w-0">
        {rotulo}
        <select className={base} value={(valor as string) ?? ""}
                onChange={(e) => aoMudar(e.target.value || null)}>
          <option value="">selecione</option>
          {p.opcoes.map((o) => <option key={o.valor} value={o.valor}>{o.rotulo}</option>)}
        </select>
      </div>
    );
  }

  if (p.tipo === "booleana") {
    const opcoes: { v: boolean | null; t: string }[] = [
      { v: true, t: "Sim" }, { v: false, t: "Não" }, { v: null, t: "Não sei" },
    ];
    return (
      <div className="flex flex-col gap-[7px] min-w-0">
        {rotulo}
        <div className="flex gap-1.5">
          {opcoes.map((o) => {
            const ativo = valor === o.v || (o.v === null && valor == null);
            return (
              <button key={String(o.v)} type="button" onClick={() => aoMudar(o.v)}
                      className={`flex-1 rounded-[var(--radius-control)] border px-3 py-2 text-[13px] transition-colors ${
                        ativo ? "border-brand bg-brand-soft text-brand font-semibold"
                              : "border-[var(--color-linha)] text-ink-soft hover:border-[#C8D4EE]"
                      }`}>
                {o.t}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  const [min, max] = p.faixa ?? [undefined, undefined];
  return (
    <div className="flex flex-col gap-[7px] min-w-0">
      {rotulo}
      <input type="number" className={`${base} nums`} min={min} max={max}
             step={p.tipo === "real" ? "any" : 1}
             value={valor == null ? "" : String(valor)}
             onChange={(e) => aoMudar(e.target.value === "" ? null : Number(e.target.value))} />
      {min != null && max != null && (
        <span className="text-[11.5px] text-ink-faint">
          entre {min.toLocaleString("pt-BR")} e {max.toLocaleString("pt-BR")}
        </span>
      )}
    </div>
  );
}

export default function CalculadoraPage() {
  const { opcoes, error: erroOpcoes, isLoading } = useOpcoes();
  const [valores, setValores] = React.useState<Valores>({});
  const [local, setLocal] = React.useState({ cep: "", endereco: "" });
  const [passo, setPasso] = React.useState(0);
  const [resultado, setResultado] = React.useState<Estimativa | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);
  const [enviando, setEnviando] = React.useState(false);

  const cidade = valores.cidade as string | undefined;
  const { lista: bairros } = useBairrosDoModelo(cidade);

  const perguntas = React.useMemo(() => {
    if (!opcoes) return [];
    return opcoes.perguntas
      .filter((p) => !(p.opcoes && p.opcoes.length <= 1))
      .map((p) => p.api === "tipo" && p.opcoes
        ? { ...p, opcoes: p.opcoes.filter((o) => RESIDENCIAIS.has(o.valor)) }
        : p);
  }, [opcoes]);

  const unicos = React.useMemo(() => {
    if (!opcoes) return {};
    return Object.fromEntries(
      opcoes.perguntas.filter((p) => p.opcoes && p.opcoes.length === 1)
                      .map((p) => [p.api, p.opcoes![0].valor]));
  }, [opcoes]);

  const daEtapa = perguntas.filter((p) => etapaDe(p) === passo);
  const faltamNaEtapa = daEtapa.filter((p) => p.obrigatoria && valores[p.api] == null);
  const faltamNoTodo = perguntas.filter((p) => p.obrigatoria && valores[p.api] == null);

  async function enviar() {
    setErro(null);
    setEnviando(true);
    try {
      const payload: Record<string, unknown> = { ...unicos };
      for (const p of perguntas) {
        const v = valores[p.api];
        if (v !== undefined && v !== "") payload[p.api] = v;
      }
      setResultado(await estimar(payload as never));
    } catch (e) {
      setErro(e instanceof ApiError
        ? (e.status === 429 ? "Muitas consultas em pouco tempo. Aguarde um instante." : e.detail)
        : String(e));
      setResultado(null);
    } finally {
      setEnviando(false);
    }
  }

  const campoLocal =
    "w-full px-3.5 py-2.5 border border-[var(--color-linha)] rounded-[var(--radius-control)] text-sm bg-surface focus:outline-none focus:border-brand focus:ring-[3px] focus:ring-brand-soft";
  const ultima = passo === ETAPAS.length - 1;

  return (
    <div className="flex flex-col gap-4 p-6 max-w-[1400px] w-full mx-auto">
      {erroOpcoes && (
        <Card titulo="Formulário indisponível">
          <p className="text-sm text-ink-soft">{erroOpcoes.message}</p>
        </Card>
      )}

      {isLoading && (
        <Card>
          <div className="flex items-center gap-2 text-sm text-ink-faint">
            <Loader2 className="w-4 h-4 animate-spin" /> carregando o formulário…
          </div>
        </Card>
      )}

      {opcoes && (
        <div className="grid xl:grid-cols-[minmax(0,1fr)_400px] gap-4 items-start">
          <Card>
            <header className="mb-6">
              <Eyebrow>Calculadora com o modelo real</Eyebrow>
              <h2 className="text-lg font-medium text-ink mt-1.5">
                Quanto deveria custar este imóvel?
              </h2>
              <p className="text-sm text-ink-soft mt-0.5">
                {perguntas.length} perguntas, que são exatamente as variáveis do
                modelo. Nenhum campo pede documento nem dado bancário.
              </p>
            </header>

            <Trilha etapas={[...ETAPAS]} atual={passo} />

            {passo === 0 && (
              <div className="grid sm:grid-cols-3 gap-[18px_20px] mb-5">
                <input className={campoLocal} placeholder="CEP" value={local.cep}
                       onChange={(e) => setLocal({ ...local, cep: e.target.value })} />
                <input className={`${campoLocal} sm:col-span-2`} placeholder="Endereço"
                       value={local.endereco}
                       onChange={(e) => setLocal({ ...local, endereco: e.target.value })} />
                <p className="sm:col-span-3 text-[11.5px] text-ink-faint leading-relaxed -mt-2">
                  CEP e endereço <b className="text-ink-soft">não entram no cálculo</b>.
                  São guardados para uso futuro — hoje o CEP está preenchido em 12%
                  da base, insuficiente para treinar.
                </p>
              </div>
            )}

            <div className="grid sm:grid-cols-2 gap-[18px_20px]">
              {daEtapa.map((p) => (
                <Campo key={p.api} p={p} valor={valores[p.api]} bairros={bairros}
                       aoMudar={(v) => setValores((s) => {
                         const n = { ...s, [p.api]: v };
                         if (p.api === "cidade") n.bairro = null;
                         return n;
                       })} />
              ))}
            </div>

            {passo === 3 && (
              <div className="bg-surface-mute rounded-[var(--radius-control)] p-4 mt-4">
                <p className="text-[12.5px] text-ink-soft leading-relaxed">
                  Informar condomínio e IPTU é o <b>segundo maior ganho</b> do
                  formulário — reduz o erro do modelo em cerca de R$ 6 mil.
                </p>
              </div>
            )}

            <div className="flex justify-between gap-3 mt-6 pt-5 border-t border-[var(--color-linha)]">
              <button type="button" disabled={passo === 0}
                      onClick={() => setPasso((p) => p - 1)}
                      className="border border-[var(--color-linha)] rounded-[var(--radius-pill)] px-4 py-[7px] text-xs font-medium text-ink-soft transition-colors hover:border-brand hover:text-brand hover:bg-brand-soft disabled:opacity-45 disabled:cursor-default disabled:hover:border-[var(--color-linha)] disabled:hover:text-ink-soft disabled:hover:bg-transparent">
                ← Voltar
              </button>
              {ultima ? (
                <button type="button" onClick={enviar}
                        disabled={enviando || faltamNoTodo.length > 0}
                        className="bg-brand text-white rounded-[var(--radius-control)] px-[22px] py-[13px] font-semibold transition-colors hover:bg-brand-escuro disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2">
                  {enviando && <Loader2 className="w-4 h-4 animate-spin" />}
                  Calcular
                </button>
              ) : (
                <button type="button" disabled={faltamNaEtapa.length > 0}
                        onClick={() => setPasso((p) => p + 1)}
                        className="bg-brand text-white rounded-[var(--radius-control)] px-[22px] py-[13px] font-semibold transition-colors hover:bg-brand-escuro disabled:opacity-40 disabled:cursor-not-allowed">
                  Continuar →
                </button>
              )}
            </div>

            {faltamNaEtapa.length > 0 && (
              <p className="text-[11.5px] text-ink-faint mt-3">
                Faltam nesta etapa: {faltamNaEtapa.map((p) => p.rotulo).join(", ")}
              </p>
            )}
            {ultima && faltamNoTodo.length > 0 && (
              <p className="text-[11.5px] text-ink-faint mt-3">
                Faltam: {faltamNoTodo.map((p) => p.rotulo).join(", ")}
              </p>
            )}
            {erro && (
              <div className="bg-brand-soft rounded-[var(--radius-control)] p-4 mt-4 text-sm text-brand-escuro">
                {erro}
              </div>
            )}
          </Card>

          <div className="xl:sticky xl:top-6">
            {resultado ? (
              <Resultado r={resultado} />
            ) : (
              <Card>
                <p className="text-sm text-ink-faint leading-relaxed">
                  Preencha as {ETAPAS.length} etapas para ver a estimativa. O
                  resultado vem sempre com faixa — nunca um número sozinho.
                </p>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
