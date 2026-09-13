// frontend/hooks/useDashboardData.ts
//
// Substitui o hook que apontava para `/api/dashboard` — rota de mock com cinco
// cidades inventadas, apagada em 2026-09-09 junto com o BFF que não existiu.
//
// Cada hook devolve `{ data, error, isLoading }` para a tela poder distinguir
// os três estados. Isso importa mais aqui do que numa aplicação comum: o
// backend responde 503 enquanto o modelo não carrega e 429 no limite de taxa,
// e nenhum dos dois é "não há dado" — são "espere". Tratar tudo como erro
// mostraria tela vazia onde o certo é tentar de novo.

import useSWR from "swr";

import {
  buscar,
  type Bairros,
  type Mercado,
  type Modelo,
  type Opcoes,
} from "@/lib/api";

/** Proveniência do número: versão, base, erro medido, viés. */
export function useModelo() {
  const { data, error, isLoading } = useSWR<Modelo>("/modelo", buscar, {
    revalidateOnFocus: false,
  });
  return { modelo: data, error, isLoading };
}

/**
 * Indicadores por bairro, da nossa base.
 *
 * `comCoordenada` filtra os que o mapa consegue desenhar. A lista completa
 * continua trazendo os sem coordenada, com `lat: null` — some-los esconderia
 * que o mapa não cobre tudo.
 */
export function useBairros(cidade?: string, comCoordenada = false) {
  const params = new URLSearchParams();
  if (cidade) params.set("cidade", cidade);
  if (comCoordenada) params.set("com_coordenada", "true");
  const q = params.toString();
  const { data, error, isLoading } = useSWR<Bairros>(
    `/bairros/indicadores${q ? `?${q}` : ""}`,
    buscar,
    { revalidateOnFocus: false },
  );
  return { bairros: data, error, isLoading };
}

/** Índice FipeZAP: rentabilidade e preço médio por cidade. Muda uma vez por mês. */
export function useMercado() {
  const { data, error, isLoading } = useSWR<Mercado>("/mercado", buscar, {
    revalidateOnFocus: false,
    // Dedup longo de propósito: a referência é mensal, não faz sentido
    // revalidar a cada foco de janela.
    dedupingInterval: 5 * 60 * 1000,
  });
  return { mercado: data, error, isLoading };
}

/**
 * O formulário inteiro, como o modelo o define.
 *
 * A calculadora renderiza A PARTIR daqui — não de uma lista escrita à mão.
 * É a garantia de D-038: variável nova no modelo vira pergunta na tela sozinha,
 * e pergunta que não alimenta o modelo não existe.
 */
export function useOpcoes() {
  const { data, error, isLoading } = useSWR<Opcoes>("/opcoes", buscar, {
    revalidateOnFocus: false,
  });
  return { opcoes: data, error, isLoading };
}

/**
 * Bairros que o MODELO conhece, para o `select` do formulário.
 *
 * Diferente de `useBairros`: aquele traz indicadores de mercado da nossa base;
 * este traz os níveis da categórica `neighborhood` do artefato. Um bairro pode
 * estar num e não no outro, e mandar ao modelo um bairro que ele não viu faz a
 * variável mais forte da base virar nulo em silêncio.
 */
export function useBairrosDoModelo(cidade?: string) {
  const { data, error, isLoading } = useSWR<{
    cidade: string | null;
    filtrado: boolean;
    n: number;
    bairros: string[];
  }>(cidade ? `/bairros?cidade=${encodeURIComponent(cidade)}` : null, buscar, {
    revalidateOnFocus: false,
  });
  return { lista: data?.bairros ?? [], filtrado: data?.filtrado ?? false, error, isLoading };
}
