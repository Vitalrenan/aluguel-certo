"""
Deduplicação de imóveis idênticos: um imóvel, um preço (a média dos anúncios).

DECISÃO DO PRODUTO (2026-08-30). Quando o mesmo imóvel aparece em dois anúncios
com preços diferentes, uma das duas coisas aconteceu: um anúncio está
desatualizado, ou o proprietário publicou um mais barato e um mais caro para
tentar ganhar mais. Nos dois casos o valor de referência é a média, não cada
anúncio isolado.

RESULTADO MEDIDO (2026-08-30, 15 folds agrupados, ensemble lgbm+xgb). Os dois
efeitos puxam em direções opostas, e a resposta é uma DIVISÃO:

  DEDUPLIQUE O ALVO.        RMSE R$ 87.104 -> 79.064 (-9,2%), erro mediano
                            11,0% -> 9,1%, dentro de +-20% 75,9% -> 79,9%.
                            Os gêmeos discordavam em R$ 95.872 de RMSE, mais
                            que o erro TOTAL do modelo. Era desacordo entre
                            anúncios, não erro de predição.

  NÃO DEDUPLIQUE O TREINO.  Treinar nas 1.189 fundidas em vez das 1.711 cruas
                            custa +10.352 R$, perdendo em 15 folds de 15
                            (p=0,0001). As duplicatas são sorteios
                            independentes em torno do mesmo valor e o modelo
                            já faz a média sozinho; descartá-las só tira
                            amostra, e a curva de aprendizado não saturou.

CUIDADO AO COMPARAR. Os 79.064 não são ganho de modelagem -- é outra régua. O
modelo é o mesmo; o alvo é que deixou de cobrar dele o que dois anúncios
discordantes tornam imprevisível. Comparar esse número com benchmark medido
contra alvo não deduplicado é errado.

O QUE NÃO ACONTECEU. Eu esperava que fundir gêmeos preenchesse buraco (um
anúncio traz `iptu_tax`, o outro não). Não preencheu: só `suites` melhorou
(21,0% -> 20,2%). A contagem de nulos não muda e a base encolhe, então a TAXA
piora por denominador -- `area_m2` foi de 15,1% para 17,9%. Fica registrado
porque eu afirmei o benefício antes de medi-lo.

CHAVE. Sem preço, obviamente -- a chave de `avaliacao.chave_de_grupo` inclui
preço e por isso só encontra duplicata de preço idêntico, que é o caso fácil.
Exige `area_m2` e `bedrooms` preenchidos: sem eles a chave fica frouxa demais e
funde imóveis que só se parecem.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Sem preço. `property_type` entra porque apartamento e casa de mesma área no
# mesmo bairro não são o mesmo imóvel.
CHAVE = ["transaction_type", "city", "neighborhood", "property_type",
         "area_m2", "bedrooms", "bathrooms", "parking_spots"]

def chave(d: pd.DataFrame, campos: list[str] | None = None) -> pd.Series:
    """
    Chave de identidade, ou NaN para a linha inelegível.

    `area_m2` é arredondada: 64,0 e 64,00 são o mesmo imóvel, e a fonte não é
    consistente na casa decimal.

    NULO EM QUALQUER CAMPO DA CHAVE TORNA A LINHA INELEGÍVEL. Não é rigor
    gratuito: `astype(str)` transforma NaN em "nan", então dois imóveis de
    bairro desconhecido e mesma metragem colapsariam num só -- sem exceção, sem
    aviso, com preço médio inventado entre imóveis que nunca foram o mesmo.
    Um teste em `tests/test_dedup.py` fixa isso: em 2026-08-30 ele pegou a
    versão que fundia R$ 300 mil com R$ 900 mil em R$ 600 mil.

    Não se afirma identidade sobre um campo que não se conhece.
    """
    campos = campos or CHAVE
    x = d.copy()
    if "area_m2" in campos:
        x["area_m2"] = x.area_m2.round(0)
    k = x[campos].astype(str).agg("|".join, axis=1)
    return k.where(d[campos].notna().all(axis=1))


def funde(d: pd.DataFrame, campos: list[str] | None = None) -> pd.DataFrame:
    """
    Um registro por imóvel. `price` vira a média dos anúncios; todo outro campo
    é preenchido pelo primeiro valor não-nulo do grupo.

    A linha inelegível (sem área ou sem quartos) passa intacta -- não sabemos se
    ela tem gêmeo, e descartá-la seria jogar fora dado por ignorância nossa.
    """
    k = chave(d, campos)
    x = d.copy()
    x["_k"] = k
    solto = x[x._k.isna()].drop(columns="_k")
    agrupavel = x[x._k.notna()]

    num = agrupavel.select_dtypes(include=[np.number]).columns
    agg = {c: "first" for c in agrupavel.columns if c != "_k"}
    agg["price"] = "mean"
    # Numérico usa a média dos não-nulos: se um anúncio informa IPTU e o outro
    # não, o fundido herda o informado.
    for c in num:
        if c not in ("price",):
            agg[c] = "mean"

    fundido = agrupavel.groupby("_k", as_index=False, sort=False).agg(agg)
    fundido = fundido.drop(columns=[c for c in ("_k",) if c in fundido.columns])
    out = pd.concat([fundido, solto], ignore_index=True)
    if "price" in out.columns:
        out["log_price"] = np.log(out.price)
    return out


def relatorio(d: pd.DataFrame, campos: list[str] | None = None) -> dict:
    """Quanto se funde, quanto se perde, quanto de buraco se preenche."""
    k = chave(d, campos)
    tam = k.map(k.value_counts())
    dentro = (tam > 1) & k.notna()
    f = funde(d, campos)
    campos_nul = ["area_m2", "bedrooms", "bathrooms", "parking_spots", "suites",
                  "condo_fee", "iptu_tax", "floor_level"]
    antes = {c: d[c].isna().mean() for c in campos_nul if c in d.columns}
    depois = {c: f[c].isna().mean() for c in campos_nul if c in f.columns}
    return {"linhas_antes": len(d), "linhas_depois": len(f),
            "perdidas": len(d) - len(f),
            "inelegiveis": int(k.isna().sum()),
            "em_grupo": int(dentro.sum()),
            "grupos": int(k[dentro].nunique()),
            "nulos_antes": antes, "nulos_depois": depois}
