/**
 * Quais pares cidade × transação estimam, e o que dizer nos que não estimam.
 *
 * A ÚNICA MUDANÇA DE FRONTEND DESTA REFATORAÇÃO. O resto das rotas, hooks e
 * componentes fica como está.
 *
 * A DISPONIBILIDADE É POR PAR, NÃO POR CIDADE. São Paulo tem 5.628 linhas de
 * venda e 2 de locação — medido em 12/09/2026. A cidade está no escopo e o par
 * (São Paulo, locação) não está. Código que trate a cidade como disponível
 * inteira oferece aluguel em São Paulo e recebe 422 depois de o usuário
 * preencher o formulário todo.
 *
 * CIDADE SEM MODELO APARECE NA LISTA. Escondê-la faria o usuário concluir que
 * não atendemos o lugar; mostrá-la desabilitada diz que ainda não atendemos, o
 * que é a verdade.
 *
 * O TEXTO VEM DO BACKEND, no campo `motivo`, e não é escrito aqui. Texto
 * duplicado nos dois lados diverge na primeira vez que alguém mudar um só.
 */

import { API, ApiError } from "@/lib/api";

export type Alvo = "venda" | "locacao";

export type Disponibilidade = {
  disponivel: boolean;
  /** Null quando disponível. Quando não, é o que a tela mostra. */
  motivo: string | null;
  /** Quantas linhas sustentam o par. Null quando a fonte não mediu. */
  n_treino_medido: number | null;
};

export type CidadeDisponivel = {
  cidade: string;
  alvos: Record<string, Disponibilidade>;
};

export type Catalogo = {
  n: number;
  disponiveis: number;
  cidades: CidadeDisponivel[];
};

export async function buscarCidades(): Promise<Catalogo> {
  const url = `${API}/cidades`;
  const r = await fetch(url);
  if (!r.ok) {
    throw new ApiError(r.status, await r.text().catch(() => ""), url);
  }
  return (await r.json()) as Catalogo;
}

/**
 * O estado de um par. Devolve indisponível para o que não conhece.
 *
 * O padrão é recusar, e não permitir: uma cidade ausente do catálogo é uma
 * que não treinamos, e deixá-la passar mandaria o pedido ao backend só para
 * receber recusa mais tarde, depois do formulário preenchido.
 */
export function estado(
  catalogo: Catalogo | undefined,
  cidade: string,
  alvo: Alvo,
): Disponibilidade {
  const ausente: Disponibilidade = {
    disponivel: false,
    motivo: "modelo estatístico ainda não disponível",
    n_treino_medido: null,
  };
  if (!catalogo) return ausente;
  const c = catalogo.cidades.find((x) => mesmaCidade(x.cidade, cidade));
  return c?.alvos?.[alvo] ?? ausente;
}

/** As transações que a cidade estima. Vazio é resposta legítima. */
export function alvosDisponiveis(
  catalogo: Catalogo | undefined,
  cidade: string,
): Alvo[] {
  if (!catalogo) return [];
  const c = catalogo.cidades.find((x) => mesmaCidade(x.cidade, cidade));
  if (!c) return [];
  return (Object.entries(c.alvos) as [Alvo, Disponibilidade][])
    .filter(([, d]) => d.disponivel)
    .map(([alvo]) => alvo);
}

/**
 * `São Paulo` e `Sao Paulo` são a mesma cidade.
 *
 * A comparação literal falha entre o que o usuário digita, o que o catálogo
 * publica e o que o caminho do modelo carrega — e falha devolvendo "cidade não
 * atendida", que é indistinguível de uma cidade de fato não atendida.
 */
export function mesmaCidade(a: string, b: string): boolean {
  return normaliza(a) === normaliza(b);
}

function normaliza(s: string): string {
  return s
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .trim()
    .toLowerCase();
}
