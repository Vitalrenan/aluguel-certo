"use client";

/**
 * Seletor de cidade e transação que sabe o que não estima.
 *
 * O SELETOR DE TRANSAÇÃO DEPENDE DO SELETOR DE CIDADE. É o detalhe que a
 * implementação erra se ninguém avisar: São Paulo tem venda e não tem locação,
 * então habilitar as duas transações sempre leva o usuário a preencher o
 * formulário inteiro para receber uma recusa no fim.
 *
 * Quem carrega o sentido aqui é o RÓTULO, nunca a matiz — a regra é do
 * `design-system.md`, e vale porque a tela não pode depender de distinguir
 * verde de vermelho para dizer o que funciona.
 */

import useSWR from "swr";

import {
  alvosDisponiveis,
  buscarCidades,
  estado,
  type Alvo,
  type Catalogo,
} from "@/lib/disponibilidade";

const ALVOS: { valor: Alvo; rotulo: string }[] = [
  { valor: "venda", rotulo: "Comprar" },
  { valor: "locacao", rotulo: "Alugar" },
];

type Props = {
  cidade: string;
  alvo: Alvo;
  onCidade: (c: string) => void;
  onAlvo: (a: Alvo) => void;
};

export function SeletorDisponivel({ cidade, alvo, onCidade, onAlvo }: Props) {
  const { data, error, isLoading } = useSWR<Catalogo>("cidades", buscarCidades);

  // Os três estados ficam separados de propósito. O backend responde 503
  // enquanto carrega, e isso não é "não há cidade" — é "espere".
  if (isLoading) return <p className="text-ink-soft">Carregando cidades…</p>;
  if (error) {
    return (
      <p className="text-ink-soft">
        Não consegui carregar a lista de cidades. Tente de novo em instantes.
      </p>
    );
  }

  const atual = estado(data, cidade, alvo);
  const habilitados = alvosDisponiveis(data, cidade);

  return (
    <div className="flex flex-col gap-4">
      <label className="flex flex-col gap-2">
        <span className="text-sm font-medium">Cidade</span>
        <select
          id="cidade"
          value={cidade}
          onChange={(e) => {
            const nova = e.target.value;
            onCidade(nova);
            // Se a transação em curso não existe na cidade nova, troca para
            // uma que exista. Deixar a escolha inválida de pé faria o botão
            // ficar desabilitado sem o usuário entender por quê.
            const possiveis = alvosDisponiveis(data, nova);
            if (possiveis.length && !possiveis.includes(alvo)) {
              onAlvo(possiveis[0]);
            }
          }}
          className="rounded-control border border-linha bg-surface px-3 py-2"
        >
          {data?.cidades.map((c) => (
            <option key={c.cidade} value={c.cidade}>
              {c.cidade}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">O que você quer fazer</legend>
        <div className="flex gap-2">
          {ALVOS.map(({ valor, rotulo }) => {
            const e = estado(data, cidade, valor);
            return (
              <button
                key={valor}
                id={`alvo-${valor}`}
                type="button"
                disabled={!e.disponivel}
                aria-pressed={alvo === valor}
                title={e.motivo ?? undefined}
                onClick={() => onAlvo(valor)}
                className={[
                  "rounded-control border px-4 py-2 text-sm",
                  alvo === valor
                    ? "border-brand bg-brand-soft font-medium"
                    : "border-linha bg-surface",
                  e.disponivel ? "" : "cursor-not-allowed opacity-50",
                ].join(" ")}
              >
                {rotulo}
                {!e.disponivel && (
                  <span className="ml-2 text-xs">indisponível</span>
                )}
              </button>
            );
          })}
        </div>
      </fieldset>

      {!atual.disponivel && (
        <div
          role="status"
          className="rounded-card border border-linha bg-surface-mute p-4"
        >
          <p className="font-medium">{atual.motivo}</p>
          <p className="mt-1 text-sm text-ink-soft">
            Estamos coletando anúncios de {cidade} para{" "}
            {alvo === "venda" ? "venda" : "aluguel"}. Enquanto não houver base
            suficiente, preferimos não dar um número a dar um número errado.
          </p>
          {habilitados.length > 0 && (
            <p className="mt-2 text-sm text-ink-soft">
              Nesta cidade já estimamos{" "}
              {habilitados.map((a) => (a === "venda" ? "venda" : "aluguel")).join(" e ")}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
