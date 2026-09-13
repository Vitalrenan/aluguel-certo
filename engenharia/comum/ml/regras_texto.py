"""
Regex para os campos que a extração por LLM mostrou render — sem custo de API.

MOTIVO. O teste de 2026-08-30 mediu as 14 features de LLM em -695 R$ de RMSE
(19/25 folds, p=0,0088), abaixo do critério de -3.000 que justificaria pagar API
para a base inteira. Mas aquele teste rodou em 600 linhas porque a extração era
manual. Regex roda nas 1.253 com descrição, e depois nas 3.776 da base inteira,
de graça. Cobertura 2x com o mesmo sinal pode valer mais que o sinal sozinho.

CAMPOS ESCOLHIDOS pelos dois critérios medidos, não por intuição:

    IV nas 600 extraidas          ganho no modelo (fold 1)
    vaga_tipo         0,3597      varanda_tipo        1,32%
    riqueza_anuncio   0,3059      riqueza_anuncio     1,24%
    vista             0,2220      estado_conservacao  1,00%
    padrao_acabamento 0,1617      padrao_acabamento   0,61%
    varanda_tipo      0,1504

`distancia_praia`, `posicao`, `lazer_predio` e `sinal_urgencia` ficam de fora:
IV abaixo de 0,07 e ganho abaixo de 0,5%.

FIDELIDADE contra os 600 rótulos do LLM, medida em 2026-08-30 (`ml.valida_regras`):

    campo                  acurácia   kappa
    varanda_tipo              98,5%   0,966
    vista                     95,2%   0,897
    vaga_tipo                 94,0%   0,920
    estado_conservacao        91,7%   0,821
    padrao_acabamento         83,5%   0,534   <- o fraco, e assim fica; ver abaixo
    riqueza_anuncio           56,5%     ---   (|dif| <= 1 em 97,2%)

PRECEDÊNCIA importa e é a fonte dos erros. Auditei o regex antigo em 10 anúncios
(2026-08-30) e os dois falsos positivos vieram de contexto ignorado:

  - "depósito caução de 3 meses" lido como depósito-cômodo. É garantia locatícia.
  - "academia" numa lista de comércio do bairro lida como amenidade do prédio.

E o caso que o regex antigo marcava ao contrário: "1 vaga demarcada (sorteio
anual)" saía como `Garagem Demarcada`, quando sorteio anual é o oposto de vaga
garantida. Por isso `sorteio` tem precedência sobre `demarcada` aqui.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd


def dobra(t) -> str:
    """Sem acento, minúsculo, espaço normalizado. Todo padrão assume esta forma."""
    if not isinstance(t, str):
        return ""
    s = unicodedata.normalize("NFKD", t)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower()


# --------------------------------------------------------------------------
# vaga_tipo — IV 0,3597, o dobro do `parking_spots` estruturado
#
# O campo numérico põe 477 de 600 imóveis na caixa "1 vaga", misturando o de
# R$ 695 mil com vaga privativa demarcada e o de R$ 260 mil com vaga rotativa.
# A ordem abaixo é a precedência, do mais específico para o mais genérico.
#
# 87,0% -> 94,0% de acurácia (kappa 0,826 -> 0,920) em 2026-08-30, por três
# correções que saíram da matriz de confusão, não de intuição.
# --------------------------------------------------------------------------
_VAGA = [
    # `sem_vaga` primeiro: "aquisicao de vaga a parte" e "nao possua vaga" são
    # negações que qualquer padrão de vaga leria como presença.
    ("sem_vaga", re.compile(
        r"\bnao\s+(possui|possua|tem|tenha|ha|conta\s+com)\s+(vaga|garagem)"
        r"|\bsem\s+(vaga|garagem)"
        r"|\bvagas?\s+a\s+parte\b|\baquisicao\s+de\s+vaga"
        r"|\bvaga\s+para\s+moto(s|cicleta)?\b(?!.*\bvaga\s+(de\s+)?(carro|auto))"
        r"|\bgaragem\s+(exclusiva\s+)?para\s+motocicleta")),
    ("sorteio", re.compile(r"\bsorteio\b")),
    ("rotativa", re.compile(r"\brotativ[ao]s?")),
    # `manobrista` é o sinal mais forte de vaga coletiva e estava faltando:
    # valet significa vaga empilhada, não privativa. Aparecia em 5 dos 12 casos
    # que a versão anterior lia como `privativa`.
    # Sem \b FINAL de propósito -- "coletivao predio" (texto colado, sem espaço,
    # como vem da fonte) e "coletivas" escapavam por causa dele.
    ("coletiva", re.compile(r"\bcoletiv[ao]s?|\bcoleti[ck]a|\bman[o]?brista|\bvalet\b"
                            r"|\bcompartilhad[ao]s?|\binsuficiente|\bempilhad[ao]s?")),
    ("privativa_demarcada", re.compile(
        r"\b(demarcad[ao]s?|escriturad[ao]s?|em\s+escritura"
        r"|consta\s+(da|na)\s+escritura)")),
    # Larga de propósito: qualquer menção a vaga/garagem que sobreviveu às regras
    # acima é vaga privativa -- foi o que os 600 rótulos mostraram. A versão
    # anterior exigia frase específica e perdia "portao eletronico e garagem",
    # "1 vaga suficiente", "vaga garantida", "vagas 1".
    # `estacionamento` fica FORA: "a rua oferece facil estacionamento" é vaga na
    # rua, o oposto do que o campo mede.
    ("privativa", re.compile(r"\bvagas?\b|\bgaragem\b|\bgaragens\b")),
]
ORD_VAGA = {"sem_vaga": 0, "nao_menciona": 1, "rotativa": 2, "sorteio": 3,
            "coletiva": 4, "privativa": 5, "privativa_demarcada": 6}


def vaga_tipo(t: str) -> str:
    for nome, padrao in _VAGA:
        if padrao.search(t):
            return nome
    return "nao_menciona"


# --------------------------------------------------------------------------
# varanda_tipo — maior ganho no modelo (1,32%) e o de maior fidelidade (98,5%)
# --------------------------------------------------------------------------
_VARANDA = [
    ("gourmet", re.compile(r"\b(varanda|terraco)\s+gourmet|varanda\s+com\s+churrasqueira")),
    ("sacada", re.compile(r"\bsacada")),
    ("comum", re.compile(r"\b(varanda|terraco|varandao)\b")),
]
ORD_VARANDA = {"nenhuma": 0, "nao_menciona": 1, "sacada": 2, "comum": 3, "gourmet": 4}


def varanda_tipo(t: str) -> str:
    for nome, padrao in _VARANDA:
        if padrao.search(t):
            return nome
    return "nao_menciona"


# --------------------------------------------------------------------------
# vista — IV 0,2220. A gradação é o que o binário perde.
# --------------------------------------------------------------------------
_PARCIAL = re.compile(r"\bvista\s+(lateral|parcial)|(lateral|parcial)\s+(para\s+o|do|pro)\s+mar")
_MAR = re.compile(r"\bvista\s+(definitiva\s+|permanente\s+|total\s+|panoramica\s+)?"
                  r"(para\s+o|pro|do|ao)\s*mar|\bvista\s+mar\b|\bfrente\s+(para\s+o\s+|ao\s+)?mar\b")
_LIVRE = re.compile(r"\bvista\s+(totalmente\s+)?livre|\bvista\s+panoramica")
ORD_VISTA = {"interna": 0, "nao_menciona": 1, "livre": 2, "mar_parcial": 3, "mar_total": 4}


def vista(t: str) -> str:
    if _PARCIAL.search(t):
        return "mar_parcial"
    if _MAR.search(t):
        return "mar_total"
    if _LIVRE.search(t):
        return "livre"
    return "nao_menciona"


# --------------------------------------------------------------------------
# padrao_acabamento — o mais fraco, kappa 0,534, e assim fica.
#
# Testei 25 tokens candidatos UM A UM contra os 600 rótulos em 2026-08-30.
# O melhor (`sofisticad`) acerta 3 e erra 3: +0,012 de kappa, cara ou coroa.
# `lavabo` acerta 5 e erra 22, custando -0,038 sozinho. Ampliar o vocabulário
# em bloco levou a acurácia de 83,5% para 75,8%.
#
# Os 40 casos `alto` que o regex perde não compartilham vocabulário -- o LLM
# julga o conjunto do anúncio, não uma palavra. Não há correção por regex aqui,
# e inventar mais termos piora. Fica registrado em vez de mascarado: se este
# campo precisar melhorar, o caminho é LLM ou classificador treinado, não regex.
# --------------------------------------------------------------------------
_ALTO = re.compile(
    r"\b(alto\s+padrao|fino\s+acabamento|porcelanato|marmore|granito"
    r"|sanca\s+de\s+gesso|gesso\s+rebaixado|iluminacao\s+(projetada|embutida)"
    r"|acabamento(s)?\s+(de\s+)?(alta\s+qualidade|alto\s+padrao|modernos?|sofisticad)"
    r"|piso\s+(de\s+)?(madeira|tabua|ipe)|closet|hidromassagem|borda\s+infinita)\b")
_SIMPLES = re.compile(r"\bpiso\s+frio\b|\b(piso|acabamento)\s+(em\s+)?ceramic[ao]\b|\bcarpete\b")
_MEDIO = re.compile(r"\b(armarios?\s+planejad|moveis\s+planejad|cozinha\s+planejad"
                    r"|box\s+(de\s+)?(vidro|blindex)|laminad[ao])\b")
ORD_ACAB = {"simples": 0, "nao_menciona": 1, "medio": 2, "alto": 3}


def padrao_acabamento(t: str) -> str:
    if _ALTO.search(t):
        return "alto"
    if _SIMPLES.search(t):
        return "simples"
    if _MEDIO.search(t):
        return "medio"
    return "nao_menciona"


# --------------------------------------------------------------------------
# estado_conservacao — a palavra "reformado" foi a que o higienizador antigo
# apagava; ela é o núcleo deste campo.
# --------------------------------------------------------------------------
_PRECISA = re.compile(r"\b(precisa\s+de\s+reforma|necessita\s+de\s+reforma"
                      r"|potencial\s+de\s+moderniz|para\s+reformar|reformar\s+e\s+personaliz)")
_OBRA = re.compile(r"\bem\s+construcao\b|\bem\s+obras\b|\bna\s+planta\b|\blancamento\b"
                   r"|\bentrega\s+(prevista|em|para)\b|\bprevisao\s+de\s+entrega\b"
                   r"|\bsera\s+entregue\b|\bfase\s+final\s+de\s+construcao\b")
_NOVO = re.compile(r"\b(novo\s+em\s+construcao|apartamento\s+novo|imovel\s+novo|predio\s+novo"
                   r"|recem\s+entregue|entregue\s+e\s+pronto\s+para\s+morar|novissimo)\b")
_REFORMADO = re.compile(r"\breformad[ao]|\bmoderniz(ado|ada|acao)\b|\brenovad[ao]\b"
                        r"|\beletrica\s+(nova|totalmente\s+reformada)|\bpintura\s+recente\b")
_BOM = re.compile(r"\b(bem\s+conservad[ao]|otimo\s+estado|excelente\s+estado"
                  r"|impecavel|super\s+arrumad[ao]|muito\s+bem\s+cuidad[ao])\b")
ORD_ESTADO = {"precisa_reforma": 0, "original": 1, "nao_menciona": 2, "bom": 3,
              "em_obra": 4, "reformado": 5, "novo": 6}


def estado_conservacao(t: str) -> str:
    if _PRECISA.search(t):
        return "precisa_reforma"
    if _NOVO.search(t):
        return "novo"
    if _OBRA.search(t):
        return "em_obra"
    if _REFORMADO.search(t):
        return "reformado"
    if _BOM.search(t):
        return "bom"
    return "nao_menciona"


# --------------------------------------------------------------------------
# riqueza_anuncio — IV 0,3059, e o único campo que NÃO precisa de semântica.
# É estatística de texto pura: quão detalhado o anunciante foi.
#
# Cortes por igualdade de distribuição marginal contra os 600 rótulos
# (2026-08-30): acerto exato 44,8% -> 56,5%, |dif| <= 1 de 92,7% -> 97,2%.
# A correlação quase não muda (0,662 -> 0,668): o binning é que estava errado,
# não o sinal. Por isso `rx_desc_chars` entra CRU ao lado deste ordinal -- a
# árvore escolhe cortes melhores que qualquer um que eu fixe aqui. O ordinal
# fica só para ser comparável ao campo do LLM.
# --------------------------------------------------------------------------
_ITEM = re.compile(r"[•\-\*\n]|\b(possui|conta\s+com|dispoe|oferece)\b")
CORTES = (130, 293, 500, 1071)


def riqueza_anuncio(t: str, cortes=CORTES) -> int:
    n = len(t)
    for i, c in enumerate(cortes, start=1):
        if n < c:
            return i
    return 5


CAMPOS = {
    "rx_vaga_tipo": (vaga_tipo, ORD_VAGA),
    "rx_varanda_tipo": (varanda_tipo, ORD_VARANDA),
    "rx_vista": (vista, ORD_VISTA),
    "rx_padrao_acabamento": (padrao_acabamento, ORD_ACAB),
    "rx_estado_conservacao": (estado_conservacao, ORD_ESTADO),
}


def aplica(d: pd.DataFrame, col: str = "desc_limpa") -> pd.DataFrame:
    """
    Acrescenta as colunas rx_*. Linha sem descrição recebe `sem_descricao`,
    nível próprio -- não ter anúncio é diferente de o anúncio não mencionar.
    """
    x = d.copy()
    t = x[col].map(dobra)
    tem = t.str.len() >= 40
    for nome, (fn, ordem) in CAMPOS.items():
        v = t.map(fn).where(tem, "sem_descricao")
        x[nome] = v
        x[f"{nome}_ord"] = v.map(ordem)
    x["rx_riqueza_anuncio"] = t.map(riqueza_anuncio).where(tem, np.nan)
    x["rx_n_itens"] = t.map(lambda s: len(_ITEM.findall(s))).where(tem, np.nan)
    x["rx_desc_chars"] = t.str.len().where(tem, np.nan)
    x["rx_n_palavras"] = t.str.count(r"\s+").add(1).where(tem, np.nan)
    return x


def colunas(d: pd.DataFrame) -> list[str]:
    return [c for c in d.columns if c.startswith("rx_")]
