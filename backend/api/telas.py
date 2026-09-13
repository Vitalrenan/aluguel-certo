"""
O contrato que as telas consomem, montado a partir da camada refinada.

POR QUE ESTE MÓDULO EXISTE, e é dívida minha. A refatoração trocou o formato de
quatro rotas -- `/mercado` virou lista onde era dicionário, `/modelo` e
`/opcoes` deixaram de existir, `/bairros/indicadores` mudou de campos -- e o
documento de arquitetura afirmava que "nenhum contrato de campo é quebrado".
Era falso. O frontend carregava e, segundos depois, quebrava em
`Cannot read properties of undefined (reading 'cidades')`.

A DIREÇÃO DO CONSERTO É ESTA, e não a inversa. O frontend é mantido por
decisão; então é o backend que se adapta ao formato que as telas já esperam. A
alternativa -- reescrever hooks, tipos e componentes para acompanhar o backend
novo -- transformaria "manter o frontend" em reescrevê-lo.

A FONTE CONTINUA SENDO UMA TABELA SÓ. Estas funções derivam os formatos antigos
de `mercado_{aaaa-mm}.parquet` e do model card. Nada aqui lê a camada tratada,
e nada aqui guarda uma segunda cópia do dado.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# Campos do `metadados.json` que a tela consome. O resto do cartão -- a
# validação cruzada, a lista de perguntas, os bairros com n insuficiente -- é
# registro de auditoria e não conteúdo de tela.
RESUMO = ("versao", "segmento", "base", "n_treino", "n_holdout",
          "treinado_em", "dropout_opcionais", "cenario_de_decisao",
          "holdout", "calibracao_holdout", "ambiente")


def _ou_nulo(v):
    """NaN vira None: JSON carrega `null` e não carrega NaN."""
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _pct(v):
    """Fração para pontos percentuais: 0,097 vira 9,7."""
    v = _ou_nulo(v)
    return None if v is None else v * 100.0


# --------------------------------------------------------------------------
# /mercado
# --------------------------------------------------------------------------

def mercado(tabela: pd.DataFrame) -> dict:
    """
    `cidades` como DICIONÁRIO indexado pelo nome, que é o que a tela faz.

    O formato novo devolvia lista, e `mercado.cidades[cidade]` num objeto de
    lista devolve `undefined` -- que é exatamente o `undefined` de onde a tela
    tentou ler `.cidades`.
    """
    # Uma linha por cidade: as colunas de índice repetem em todo bairro.
    por_cidade = tabela.drop_duplicates(subset=["cidade"]).set_index("cidade")

    cidades, sem_rentabilidade = {}, []
    for nome, r in por_cidade.iterrows():
        y = _ou_nulo(r.get("fipezap_yield_mensal"))
        if y is None:
            sem_rentabilidade.append(nome)

        mes_fz = _ou_nulo(r.get("mes_referencia_fipezap"))
        mes_ipca = _ou_nulo(r.get("mes_referencia_ipca"))

        cidades[nome] = {
            "referencia": mes_fz or r.get("mes_referencia"),
            # POR CAMPO, porque eles não terminam no mesmo mês: a FipeZAP
            # publica preço até agosto e rentabilidade até julho. Uma
            # referência só faria a tela datar o preço com o mês do yield.
            "referencias": {
                "venda_m2": mes_fz, "val12": mes_fz,
                "locacao_m2": mes_fz, "locacao_val12": mes_fz,
                "yield_mensal": mes_fz, "ipca_12m": mes_ipca,
            },
            "venda_m2": _ou_nulo(r.get("fipezap_venda_m2")),
            # A UNIDADE DIFERE POR CAMPO, e a tela é quem manda.
            #
            # `val12` é lido com fator 1 e depois usado como `1 + v/100`, então
            # tem de chegar em PONTOS PERCENTUAIS: 9,7 e não 0,097. Já
            # `yield_*` é lido com fator 100, então chega como FRAÇÃO.
            #
            # A FipeZAP publica os dois como fração. Mandar `val12` cru faria a
            # tela mostrar 0,10% onde são 9,7% -- número plausível, silencioso
            # e errado por duas ordens de grandeza.
            "val12": _pct(r.get("fipezap_venda_var12")),
            "locacao_m2": _ou_nulo(r.get("fipezap_locacao_m2")),
            "locacao_val12": _pct(r.get("fipezap_locacao_var12")),
            "yield_mensal": y,
            # Anualiza COMPONDO, não multiplicando por 12. Doze vezes a taxa
            # mensal subestima o ano, e a diferença cresce com a taxa.
            "yield_anual": (None if y is None else (1 + y) ** 12 - 1),
            "ipca_12m": _ou_nulo(r.get("ipca_12m")),
            "recorte_ipca": _ou_nulo(r.get("recorte_ipca")),
            "serie_meses": int(
                (tabela.cidade == nome).sum() and
                tabela[tabela.cidade == nome].mes_referencia.nunique()),
            "primeiro_mes": str(tabela[tabela.cidade == nome]
                                .mes_referencia.min()),
        }

    return {
        "fonte": {"nome": "Índice FipeZAP",
                  "url": "https://www.fipe.org.br/pt-br/indices/fipezap/",
                  "indicador": "preço médio por m² e rentabilidade mensalizada"},
        "referencia": str(tabela.mes_referencia.max()),
        "coletado_em": str(tabela.mes_referencia.max()),
        "cidades_sem_rentabilidade": sorted(sem_rentabilidade),
        "cidades": cidades,
    }


# --------------------------------------------------------------------------
# /bairros/indicadores
# --------------------------------------------------------------------------

def _faixa(serie: pd.Series) -> pd.Series:
    """
    Abaixo, média ou acima, por terço do R$/m² DENTRO da cidade.

    Dentro da cidade e não no conjunto: comparar um bairro de Santos com um de
    São Paulo diria mais sobre as duas cidades do que sobre os dois bairros.
    """
    if serie.notna().sum() < 3:
        return pd.Series([None] * len(serie), index=serie.index)
    q1, q2 = serie.quantile(1 / 3), serie.quantile(2 / 3)
    return serie.map(lambda v: None if pd.isna(v)
                     else ("abaixo" if v <= q1 else
                           "acima" if v >= q2 else "media"))


def bairros(tabela: pd.DataFrame, cidade: str | None = None,
            com_coordenada: bool = False) -> dict:
    # AS LINHAS SÓ DE ÍNDICE FICAM DE FORA. A tabela carrega as cidades da
    # FipeZAP onde não coletamos, com `bairro` nulo, para o mapa do front ter o
    # preço delas. Elas não são bairros e não são base nossa.
    #
    # Deixá-las passar fazia a tela dizer "52 com base própria" onde são 6 --
    # e "base própria" é justamente o que separa o que medimos do que só
    # repetimos de terceiro.
    tabela = tabela[tabela.bairro.notna()]
    d = tabela.copy()
    d["faixa"] = (d.groupby("cidade", group_keys=False)["preco_m2_mediano"]
                  .apply(_faixa))

    total = len(d)
    com_coord_total = int(d.lat.notna().sum())

    if cidade:
        from api.resolucao import slug
        d = d[d.cidade.map(slug) == slug(cidade)]
    if com_coordenada:
        d = d[d.lat.notna()]

    linhas = [{
        "nome": r.bairro,
        "cidade": r.cidade,
        "imoveis": int(r.n_anuncios),
        "preco_mediano": _ou_nulo(r.preco_mediano),
        "preco_m2_mediano": _ou_nulo(r.preco_m2_mediano),
        "lat": _ou_nulo(r.lat),
        "lon": _ou_nulo(r.lon),
        # `n_geo` era a contagem de anúncios geocodificados do bairro. A tabela
        # guarda o centroide, não quantos pontos o produziram; devolver o total
        # de anúncios seria mentir sobre a evidência do ponto.
        "n_geo": int(r.n_anuncios) if pd.notna(r.lat) else 0,
        "origem_geo": "cnefe2022" if pd.notna(r.lat) else None,
        "faixa": _ou_nulo(r.faixa),
    } for r in d.sort_values(["cidade", "n_anuncios"],
                             ascending=[True, False]).itertuples()]

    return {
        "base": str(tabela.mes_referencia.max()),
        "segmento": "todos",
        "gerado_em": str(tabela.mes_referencia.max()),
        "geocodificacao": {
            "fonte": "CNEFE 2022 (IBGE), via CEP e via logradouro",
            "por_cep": com_coord_total,
            "por_logradouro": 0,
            "sem_coordenada": total - com_coord_total,
            "nota": "bairro sem coordenada fica com lat nula e não recebe o "
                    "centroide da cidade: ponto inventado é indistinguível de "
                    "medido no mapa",
        },
        "resumo": {
            "n_bairros": total,
            "n_bairros_com_coordenada": com_coord_total,
            "n_imoveis": int(tabela.n_anuncios.sum()),
            "n_imoveis_em_bairro_com_coordenada":
                int(tabela.loc[tabela.lat.notna(), "n_anuncios"].sum()),
            "cidades": sorted(tabela.cidade.unique().tolist()),
        },
        "filtro": {"cidade": cidade, "com_coordenada": com_coordenada},
        "n": len(linhas),
        "bairros": linhas,
    }


# --------------------------------------------------------------------------
# /modelo  e  o bloco `modelo` da estimativa
# --------------------------------------------------------------------------

def metadados(cartao: dict) -> dict:
    """
    O cartão traduzido para o `metadados.json` que a tela conhece.

    O cartão é a fonte -- ele tem o bloco `limites`, que o formato antigo nunca
    teve. Esta função só reetiqueta; não recalcula nada, para que os dois nunca
    discordem.
    """
    ident, escopo = cartao["identificacao"], cartao["escopo"]
    desempenho = cartao.get("desempenho") or {}
    return {
        "versao": ident["versao"],
        # `segmento` era `venda_ate800k`; agora o recorte é o par, e é isso que
        # a tela deve mostrar.
        "segmento": f"{ident['cidade']}/{ident['alvo']}",
        "base": (cartao.get("dados") or {}).get("abt", ""),
        "n_treino": escopo["n_treino"],
        "n_holdout": escopo.get("n_holdout", 0),
        "treinado_em": (cartao.get("ambiente") or {}).get("gerado_em", ""),
        "dropout_opcionais": 0.0,
        "cenario_de_decisao": "sem_opcionais",
        "holdout": {"sem_opcionais": desempenho},
        # O cartão não guarda calibração por decil. Devolver lista vazia é
        # honesto; inventar decis faria a tela desenhar um gráfico de nada.
        "calibracao_holdout": cartao.get("calibracao_por_decil") or [],
        "ambiente": cartao.get("ambiente") or {},
        "limites": cartao.get("limites") or {},
    }


def resumo_do_modelo(cartao: dict) -> dict:
    """O bloco `modelo` que acompanha cada estimativa."""
    ident, escopo = cartao["identificacao"], cartao["escopo"]
    ho = (cartao.get("desempenho") or {})
    return {
        "versao": ident["versao"],
        "base": (cartao.get("dados") or {}).get("abt", ""),
        "segmento": f"{ident['cidade']}/{ident['alvo']}",
        "n_treino": escopo["n_treino"],
        "treinado_em": (cartao.get("ambiente") or {}).get("gerado_em", ""),
        "rmse_holdout": ho.get("rmse_brl"),
        "mae_holdout": ho.get("mae_brl"),
        "erro_mediano_holdout": ho.get("erro_mediano"),
        "dentro_20pct_holdout": ho.get("dentro_20pct"),
        "cobertura_intervalo": ho.get("cobertura_intervalo"),
        "vies_brl_holdout": ho.get("vies_brl"),
        "vies_mediano_holdout": ho.get("vies_mediano"),
        # Campos novos, acrescentados sem tirar nada: a tela ignora o que não
        # conhece, e quem quiser passa a ter de onde ler a proveniência.
        "cidade": ident["cidade"],
        "alvo": ident["alvo"],
        "escopo_treino": escopo["escopo_treino"],
    }


# --------------------------------------------------------------------------
# o aluguel, a partir do rendimento publicado
# --------------------------------------------------------------------------

def aluguel(preco: float, lo: float, hi: float, cidade: str,
            tabela: pd.DataFrame) -> tuple[dict | None, str | None]:
    """
    Aluguel como ÂNCORA DE MERCADO, não como previsão.

    O modelo prevê preço de venda. O aluguel sai do rendimento publicado pela
    FipeZAP aplicado a esse preço -- premissa de mercado, e por isso o bloco
    carrega `tipo: "ancora_de_mercado"` em vez de se passar por estimativa.

    Cidade sem rentabilidade publicada não recebe número nenhum: devolve
    `(None, motivo)`, e o motivo vira ressalva na tela.
    """
    from api.resolucao import slug

    linha = tabela[tabela.cidade.map(slug) == slug(cidade)]
    if not len(linha):
        return None, f"sem indicador de mercado para {cidade}"

    r = linha.iloc[0]
    y = _ou_nulo(r.get("fipezap_yield_mensal"))
    if y is None:
        return None, (f"a FipeZAP não publica rentabilidade para {cidade}, "
                      "então o aluguel não é estimado")

    # O RENDIMENTO JÁ É FRAÇÃO MENSAL do preço de venda -- a FipeZAP publica
    # mensalizado, e é por isso que o coletor lê a coluna de rentabilidade em
    # vez de dividir a de preço. Dividir por 100 aqui daria um aluguel cem
    # vezes menor: R$ 70 num imóvel de um milhão, em vez de R$ 7.000.
    p10 = _ou_nulo(r.get("fipezap_yield_p10")) or y
    p90 = _ou_nulo(r.get("fipezap_yield_p90")) or y

    return {
        "estimativa": round(preco * y, 2),
        "envelope": {"min": round(lo * p10, 2), "max": round(hi * p90, 2)},
        "envelope_cobertura_medida": None,
        "largura_do_envelope": round(hi * p90 - lo * p10, 2),
        "tipo": "ancora_de_mercado",
        "yield": {
            "cidade": cidade,
            "mensal": y,
            "fonte": "Índice FipeZAP",
            "referencia": str(_ou_nulo(r.get("mes_referencia_fipezap")) or ""),
            "medido_em": str(_ou_nulo(r.get("mes_referencia_fipezap")) or ""),
            "n": None,
            "ressalvas": [
                "a faixa usa a dispersão relativa medida em Santos aplicada ao "
                "centro da FipeZAP; a FipeZAP não publica dispersão entre imóveis",
            ],
        },
        "nota": "o aluguel não é previsto pelo modelo: é o rendimento "
                "publicado da cidade aplicado ao preço estimado",
    }, None
