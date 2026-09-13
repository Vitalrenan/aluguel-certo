/**
 * Os quatro indicadores, portados VERBATIM do artefato `4027f9a0`.
 *
 * A primeira versão deste arquivo foi escrita de memória e errou quatro coisas
 * que mudavam a tela. Ficam registradas porque são o tipo de detalhe que se
 * perde ao reimplementar "parecido":
 *
 *   1. **Cada indicador tem a SUA rampa.** Não há uma rampa azul única.
 *      Valorização é ciano, rentabilidade é um azul mais suave, e
 *      `val_ipca` é divergente ciano→azul. Usar a mesma rampa nos quatro
 *      apagava a distinção entre eles.
 *   2. **`val_ipca` é uma RAZÃO, com pivô 1** — "quantas vezes o imóvel rendeu
 *      acima da inflação", formatada com `×`. Eu tinha feito subtração em
 *      pontos percentuais, que é outro indicador.
 *   3. **`curto` formata em pt-BR**, com vírgula. `toFixed(1)` devolvia "8.4%"
 *      num produto em português.
 *   4. **A escala é calculada sobre TODAS as cidades**, não sobre as cinco do
 *      topo. Calculando sobre o topo, as cinco barras varriam a rampa inteira
 *      e o gráfico virava arco-íris; sobre o conjunto, as cinco maiores caem
 *      todas na ponta escura, que é o que a referência mostra.
 */

import type { Mercado } from "@/lib/api";
import { IPCA_12M } from "@/lib/mock/mercado";

export type ChaveIndicador = "preco" | "val12" | "val_ipca" | "rent";

export interface ValorCidade { valor: number | null; mock: boolean }

export interface LinhaCidade {
  nome: string;
  uf: string;
  preco: ValorCidade;
  val12: ValorCidade;
  val_ipca: ValorCidade;
  rent: ValorCidade;
  rent_mes: ValorCidade;
  /** Mês mais recente da cidade. Cada campo tem o seu em `referencias`. */
  referencia?: string;
  /** Mês por campo: preço e valorização vão um mês à frente da rentabilidade. */
  referencias?: Record<string, string>;
  base_propria: boolean;
}

const num = (n: number) => n.toLocaleString("pt-BR");
const dec = (v: number, min = 2, max = 2) =>
  v.toLocaleString("pt-BR", { minimumFractionDigits: min, maximumFractionDigits: max });

export interface DefIndicador {
  rot: string;
  sub: string;
  fmt: (v: number) => string;
  curto: (v: number) => string;
  unid: string;
  tipo: "seq" | "div";
  piv?: number;
  rampa: string[];
  /**
   * Por que este indicador ainda é ilustrativo. VAI PARA A TELA. Presente
   * apenas enquanto ele depender de mock — quando a fonte chegar, some a
   * linha e some o selo junto. A nota do card lê daqui em vez de trazer o
   * motivo escrito à mão, que é como ela ficou dizendo por duas semanas que a
   * valorização era ilustrativa depois de ela já ser real.
   */
  mock?: string;
}

export const INDICADORES: Record<ChaveIndicador, DefIndicador> = {
  preco: {
    rot: "Preço do m²", sub: "venda residencial",
    fmt: (v) => "R$ " + num(Math.round(v)), unid: "/m²",
    curto: (v) => v >= 1000
      ? "R$ " + (v / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mil"
      : "R$ " + v,
    tipo: "seq",
    rampa: ["#DCE6FA", "#B4C7F2", "#7C9AE6", "#4571D6", "#1B3FA8"],
  },
  val12: {
    rot: "Valorização anual", sub: "variação do m² em 12 meses",
    fmt: (v) => dec(v) + "%",
    curto: (v) => v.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%",
    unid: " a.a.",
    tipo: "seq",
    rampa: ["#DDF0F8", "#A8DCEC", "#5FBEDC", "#1E96BE", "#0A6C8C"],
  },
  val_ipca: {
    rot: "Valorização ÷ inflação",
    sub: "quantas vezes o imóvel rendeu acima da inflação",
    fmt: (v) => dec(v) + "×",
    curto: (v) => v.toLocaleString("pt-BR", { maximumFractionDigits: 2 }) + "×",
    unid: " a inflação",
    tipo: "div", piv: 1,
    rampa: ["#0A6C8C", "#63BFD8", "#DDE4F0", "#6B92E4", "#1B3FA8"],
    mock: "a valorização vem da FipeZAP, mas o IPCA por cidade ainda não é coletado",
  },
  rent: {
    rot: "Rentabilidade do aluguel", sub: "aluguel ÷ valor de mercado",
    fmt: (v) => dec(v) + "%",
    curto: (v) => v.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "%",
    unid: " a.a.",
    tipo: "seq",
    rampa: ["#E1E7F9", "#BCC8EF", "#8B9FE0", "#5573CE", "#2B4BA6"],
  },
};

export interface Escala {
  mn: number;
  mx: number;
  cortes: number[];
  ind: DefIndicador;
  faixa: (v: number | null) => number | null;
  cor: (v: number | null) => string;
  ticks: () => string[];
}

/** Portada verbatim. Quintis para `seq`; cortes ancorados no pivô para `div`. */
export function escala(valores: (number | null)[], ind: DefIndicador): Escala | null {
  const vs = valores.filter((v): v is number => v != null).sort((a, b) => a - b);
  if (!vs.length) return null;
  const mn = vs[0], mx = vs[vs.length - 1];
  let cortes: number[];
  if (ind.tipo === "div") {
    // Cortes ancorados no pivô mas espremidos dentro de [mn,mx], senão a
    // legenda mostra marcas fora de ordem.
    const piv = ind.piv ?? 0;
    const ab = Math.max(piv - mn, 1e-9), ac = Math.max(mx - piv, 1e-9);
    cortes = [mn + ab * 0.45, piv - ab * 0.10, piv + ac * 0.10, piv + ac * 0.50];
  } else {
    const p = (mx - mn) / 5 || 1;
    cortes = [mn + p, mn + 2 * p, mn + 3 * p, mn + 4 * p];
  }
  return {
    mn, mx, cortes, ind,
    faixa(v) {
      if (v == null) return null;
      let i = 0;
      while (i < cortes.length && v > cortes[i]) i++;
      return i;
    },
    cor(v) {
      const f = this.faixa(v);
      return f == null ? "#C6D0E6" : ind.rampa[f];
    },
    ticks() {
      return [mn, ...cortes, mx].map((v) => ind.curto(v));
    },
  };
}

/**
 * Mês de referência de UM campo, o mais recente entre as cidades.
 *
 * Existe porque os campos não terminam no mesmo mês: a FipeZAP publica preço e
 * variação de 2026-08 e rentabilidade só até 2026-07. Um único `referencia` na
 * tela dataria a rentabilidade com o mês errado — que é exatamente o defeito
 * que o coletor acabou de corrigir do lado de lá.
 */
export function mesDe(
  mercado: Mercado | undefined, campo: string,
): string | null {
  let mx: string | null = null;
  for (const c of Object.values(mercado?.cidades ?? {})) {
    const m = c.referencias?.[campo];
    if (m && (mx == null || m > mx)) mx = m;
  }
  return mx;
}

/**
 * Monta as linhas a partir de `GET /mercado`.
 *
 * TRÊS DOS QUATRO INDICADORES SÃO REAIS desde 2026-09-10. Preço, valorização e
 * rentabilidade vêm da FipeZAP e não têm mais fallback: cidade que a fonte não
 * publica fica `null` e some do ranking daquele indicador, em vez de entrar com
 * um número inventado competindo com os medidos. Eram 20 cidades sem
 * rentabilidade recebendo valor de enfeite.
 *
 * `val_ipca` é a razão entre a valorização e a inflação, com 1 significando
 * "empatou com o IPCA" — não a diferença entre as duas. Continua marcado
 * porque o IPCA por cidade ainda é mock, mesmo com a valorização já real.
 */
export function combina(
  mercado: Mercado | undefined,
  cidadesDoMapa: string[],
  cidadesComBase: Set<string>,
): LinhaCidade[] {
  return cidadesDoMapa.map((chave) => {
    const [nome, uf] = chave.split("|");
    const real = mercado?.cidades?.[nome];

    const pct = (v: number | null | undefined, fator = 1): ValorCidade =>
      ({ valor: v != null ? v * fator : null, mock: false });

    const v12 = pct(real?.val12);
    const ipca = IPCA_12M[nome] ?? null;
    const razao = v12.valor != null && ipca != null
      ? (1 + v12.valor / 100) / (1 + ipca / 100) : null;

    return {
      nome, uf,
      preco: pct(real?.venda_m2),
      val12: v12,
      // `mock` só quando HÁ valor: um null aqui é ausência de dado, não valor
      // ilustrativo, e a tela conta as duas coisas em contadores diferentes.
      val_ipca: { valor: razao, mock: razao != null },
      rent: pct(real?.yield_anual, 100),
      rent_mes: pct(real?.yield_mensal, 100),
      referencia: real?.referencia,
      referencias: real?.referencias,
      base_propria: cidadesComBase.has(nome),
    };
  });
}
