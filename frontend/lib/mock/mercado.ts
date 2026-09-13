/**
 * O que a API ainda não entrega — e só isso.
 *
 * O projeto proíbe inventar número. O que ele permite, e o `design-system.md`
 * §10 prevê, é **mock declarado**: valor ilustrativo que chega à tela com
 * `<SeloMock>` colado nele. A diferença entre as duas coisas é se o usuário
 * consegue distinguir.
 *
 * ENCOLHEU EM 2026-09-10. O coletor da FipeZAP passou a extrair a variação em
 * 12 meses (coluna 12) e a manter as cidades que têm preço mas não têm
 * rentabilidade — antes elas eram descartadas inteiras. Com isso saíram daqui:
 *
 *   - `VALORIZACAO_12M` — a FipeZAP publica, e agora é lida. Os valores que
 *     estavam aqui estavam errados: Itapema figurava 14,28% contra 4,85%
 *     reais, e Vitória 7,42% contra 10,16%;
 *   - `PRECO_M2_ILUSTRATIVO` — 57 das 57 cidades têm preço real;
 *   - `RENT_MENSAL_ILUSTRATIVO` — inventar rentabilidade para Itapema,
 *     Balneário Camboriú e Itajaí era pior que não ter: a FipeZAP não publica
 *     essas três, e um número derivado de nada entrava no ranking competindo
 *     com os medidos. Cidade sem rentabilidade agora simplesmente não aparece
 *     onde a rentabilidade é o assunto.
 *
 * SOBRA UM. O IPCA por cidade exige a API do SIDRA/IBGE, que ainda não foi
 * escrita — o guia está em `referencias_fipezap_homepage.md`. Como
 * `val_ipca` é a razão entre a valorização e a inflação, ele herda a marca
 * mesmo com a valorização já real.
 */

/**
 * IPCA acumulado em 12 meses, por cidade. ILUSTRATIVO — falta o SIDRA.
 *
 * A lista tem de cobrir TODAS as chaves de `GEO.cidades`. Cidade de fora
 * aparece no mapa sem `val_ipca` e cai no contador de "sem dado", que diz
 * "a fonte não publica" — mentira, o buraco é aqui. Foi o que aconteceu com
 * Guarujá: a lista trazia Praia Grande, que não está no mapa.
 */
export const IPCA_12M: Record<string, number> = {
  "Santos": 4.44, "São Paulo": 4.81, "Vitória": 4.52, "Itapema": 4.38,
  "Balneário Camboriú": 4.38, "Florianópolis": 4.38, "Itajaí": 4.38,
  "Fortaleza": 4.66, "Aracaju": 4.71, "Goiânia": 4.58, "São Luís": 4.83,
  "Campo Grande": 4.29, "Rio de Janeiro": 4.95, "Brasília": 4.61,
  // Baixada Santista: o IBGE não publica IPCA próprio, usa-se o de SP.
  "Guarujá": 4.44, "Praia Grande": 4.44,
};
