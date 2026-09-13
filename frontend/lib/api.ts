/**
 * Cliente do serviço de inferência.
 *
 * NÃO HÁ BFF. O navegador fala direto com o FastAPI, por decisão registrada.
 * Duas consequências que este arquivo carrega:
 *
 *   - a URL usa `NEXT_PUBLIC_`, porque quem chama é o cliente. Sem o prefixo o
 *     Next não a expõe ao navegador e a chamada sai para `undefined/...`;
 *   - os tipos abaixo são a borda do contrato. Sem proxy para reformatar, o
 *     que o Python devolve é o que o React consome — e por isso os nomes são
 *     os do backend (`preco_mediano`, `yield_mensal`), não uma tradução. Toda
 *     tradução é um lugar a mais para divergir em silêncio.
 *
 * O backend exige CORS para isto funcionar; ele está configurado com
 * `ALUGUELCERTO_ORIGINS`, que por padrão libera `http://localhost:3000`.
 */

export const API =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

/** Erro com o status HTTP preservado — a tela decide o que dizer em cada um. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly url: string,
  ) {
    super(`${status} em ${url}: ${detail}`);
    this.name = "ApiError";
  }
}

export async function buscar<T>(caminho: string): Promise<T> {
  const url = `${API}${caminho}`;
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) {
    // 503 é "modelo não carregado", 429 é limite de taxa, 404 é cidade sem
    // dado. Cada um tem estado próprio na tela; engolir todos como "erro"
    // apagaria a diferença entre "espere" e "isto não existe".
    let detail = r.statusText;
    try {
      detail = (await r.json())?.detail ?? detail;
    } catch {
      /* resposta sem corpo JSON */
    }
    throw new ApiError(r.status, detail, url);
  }
  return (await r.json()) as T;
}

export async function estimar(pedido: PedidoEstimativa): Promise<Estimativa> {
  const url = `${API}/estimativa`;
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pedido),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const corpo = await r.json();
      // 422 do Pydantic vem como lista de erros por campo.
      detail = Array.isArray(corpo?.detail)
        ? corpo.detail.map((e: { msg: string }) => e.msg).join("; ")
        : (corpo?.detail ?? detail);
    } catch {
      /* sem corpo */
    }
    throw new ApiError(r.status, detail, url);
  }
  return (await r.json()) as Estimativa;
}

// --------------------------------------------------------------------------
// Tipos — espelham `serving/contrato.py`
// --------------------------------------------------------------------------

export interface Intervalo {
  min: number;
  max: number;
}

export interface Yield {
  cidade: string;
  mensal: number;
  mensal_p10: number;
  mensal_p90: number;
  fonte: string;
  referencia: string;
  medido_em: string;
  n: number | null;
  ressalvas: string[];
}

export interface Aluguel {
  estimativa: number;
  /** NÃO se chama `intervalo`: o do preço tem cobertura medida, este não. */
  envelope: Intervalo;
  envelope_cobertura_medida: number | null;
  largura_do_envelope: number;
  /** Sempre "ancora_de_mercado". A tela não pode dizer "o modelo calculou". */
  tipo: string;
  yield: Yield;
  nota: string;
}

export interface ModeloResumo {
  versao: string;
  base: string;
  segmento: string;
  n_treino: number;
  treinado_em: string;
  rmse_holdout: number | null;
  mae_holdout: number | null;
  erro_mediano_holdout: number | null;
  dentro_20pct_holdout: number | null;
  /** Fração medida no holdout. NÃO é 80%. Ver D-038. */
  cobertura_intervalo: number | null;
  vies_brl_holdout: number | null;
  vies_mediano_holdout: number | null;
}

export interface Estimativa {
  estimativa: number;
  intervalo: Intervalo;
  /** `null` onde não há yield com fonte declarada. Nunca uma taxa padrão. */
  aluguel: Aluguel | null;
  modelo: ModeloResumo;
  campos_ausentes: string[];
  ressalvas: string[];
}

export interface PedidoEstimativa {
  cidade: string;
  bairro: string;
  tipo: string;
  area_m2: number;
  dormitorios: number;
  banheiros: number;
  suites: number;
  vagas: number;
  transacao?: "venda" | "locacao";
  vaga_tipo?: string;
  vista?: string;
  andar?: number | null;
  varanda?: boolean | null;
  piscina?: boolean | null;
  elevador?: boolean | null;
  sauna?: boolean | null;
  academia?: boolean | null;
  quintal?: boolean | null;
  ar_condicionado?: boolean | null;
  condominio?: number | null;
  iptu?: number | null;
}

/** `GET /modelo` — resposta compacta. `?completo=true` traz o registro inteiro. */
export interface Modelo {
  versao: string;
  segmento: string;
  base: string;
  n_treino: number;
  n_holdout: number;
  treinado_em: string;
  dropout_opcionais: number;
  cenario_de_decisao: string;
  holdout: Record<string, Record<string, number>>;
  calibracao_holdout: {
    decil: number;
    n: number;
    real_mediano: number;
    previsto_mediano: number;
    vies_pct: number;
  }[];
}

export interface Bairro {
  nome: string;
  cidade: string;
  imoveis: number;
  preco_mediano: number;
  preco_m2_mediano: number | null;
  /** `null` quando não há coordenada. O mapa desenha só o que existe. */
  lat: number | null;
  lon: number | null;
  n_geo: number;
  origem_geo: string | null;
  faixa: "abaixo" | "media" | "acima" | null;
}

export interface Bairros {
  base: string;
  segmento: string;
  gerado_em: string;
  geocodificacao: {
    fonte: string;
    por_cep: number;
    por_logradouro: number;
    sem_coordenada: number;
    nota: string;
  };
  resumo: {
    n_bairros: number;
    n_bairros_com_coordenada: number;
    n_imoveis: number;
    n_imoveis_em_bairro_com_coordenada: number;
    cidades: string[];
  };
  n: number;
  bairros: Bairro[];
}

export interface IndicadorCidade {
  /** Mês mais recente entre os campos. Cada um tem o seu em `referencias`. */
  referencia: string;
  /**
   * Os campos NÃO terminam no mesmo mês: preço e variação vão um mês à frente
   * da rentabilidade. Por isso a referência é por campo, e a tela cita a do
   * campo que está mostrando.
   */
  referencias: Record<string, string>;
  venda_m2: number | null;
  /** Variação do m² em 12 meses, em %. Vem da FipeZAP, não é mais mock. */
  val12: number | null;
  locacao_m2: number | null;
  locacao_val12: number | null;
  /** `null` nas 20 cidades que a FipeZAP não publica rentabilidade. */
  yield_mensal: number | null;
  yield_anual: number | null;
  serie_meses: number;
  primeiro_mes: string;
}

export interface Mercado {
  fonte: { nome: string; url: string; indicador: string };
  referencia: string;
  coletado_em: string;
  cidades_sem_rentabilidade: string[];
  cidades: Record<string, IndicadorCidade>;
}

export interface Opcao {
  valor: string;
  rotulo: string;
}

export interface Pergunta {
  api: string;
  rotulo: string;
  tipo: string;
  obrigatoria: boolean;
  faixa: number[] | null;
  opcoes: Opcao[] | null;
  delta_rmse: number | null;
  nota: string;
}

export interface Opcoes {
  perguntas: Pergunta[];
  vocabulario: Record<string, Opcao[]>;
}
