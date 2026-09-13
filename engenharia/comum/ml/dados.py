"""
Carga, segmentação e feature engineering — §2 e §3 do plano.

Regra que organiza o módulo: **o que depende do alvo é calculado DENTRO do fold
de treino.** Target encoding fora do fold é a forma mais comum de inflar um
benchmark de AutoML sem perceber, e por isso ela mora em `ajusta_no_treino()`,
que recebe só o treino, e nunca no carregamento.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

from comum import vocab

RESIDENCIAL = {"Apartamento", "Apartamento Residencial", "Cobertura", "Kitnet",
               "Studio", "Flat", "Apartamento Duplex", "Loft", "Sala Living"}
CASA = {"Casa", "Sobrado", "Casa De Condominio", "Casa Residencial",
        "Casa Terrea", "Casa Comercial"}
COMERCIAL = {"Sala comercial", "Sala", "Loja", "Predio", "Prédio", "Galpao",
             "Galpão", "Conjunto comercial", "Ponto comercial", "Deposito"}

NAO_PREDITOR = {"property_id", "link", "source_domain", "source_platform",
                "state", "extraction_date", "price", "log_price", "cep",
                "grupo", "familia_alvo", "_holdout"}
# `_holdout` entra explicitamente porque `matriz()` protege por prefixo `_` mas
# os scripts de fase montam a lista de colunas na mao e nao herdavam a regra --
# em 2026-08-30 ele estava sendo passado como preditor booleano. Nao vaza preco
# (corr +0,0296 com log_price, KS p=0,22), mas e artefato de processo, nao
# atributo de imovel, e nao tem por que a arvore ve-lo.

CHAVE = ["area_m2", "bedrooms", "bathrooms", "parking_spots",
         "condo_fee", "iptu_tax", "floor_level", "suites"]

# O que `basicas()` deriva de cada campo. FONTE ÚNICA: quem apaga o campo tem de
# apagar isto junto. `condo_por_m2 * area_m2` devolve `condo_fee`, e `area_m2`
# nunca é apagada -- apagar só o campo esconde o valor da leitura humana e não
# do modelo. Ver `avaliacao.apaga_campos`, que é onde o defeito de 2026-09-07
# foi corrigido.
DERIVADAS = {"condo_fee": ("condo_por_m2",), "iptu_tax": ("iptu_por_m2",)}


_FAMILIA_CANONICA = {
    "apartamento": "residencial", "kitnet": "residencial",
    "cobertura": "residencial", "flat": "residencial",
    "studio": "residencial", "loft": "residencial",
    "casa": "casa", "sobrado": "casa", "chacara": "casa",
    "sala": "comercial", "loja": "comercial", "galpao": "comercial",
    "predio": "comercial", "garagem": "comercial",
    "terreno": "terreno",
}


def familia(tipo: str) -> str:
    """
    Tipo do anúncio -> família. Normaliza ANTES de classificar.

    A versão anterior comparava a string exata contra conjuntos capitalizados
    mantidos à mão (`'Apartamento'`, `'Cobertura'`). Isso funcionava por acaso:
    as quatro plataformas originais emitiam tipo capitalizado.

    O VivaReal emite minúsculo, vindo da URL. Resultado medido em 2026-08-31:
    os 2.995 anúncios coletados viraram `familia = 'outro'` e o filtro
    residencial descartou TODOS. Três mil linhas coletadas, normalizadas e
    gravadas, invisíveis para o modelo -- sem exceção, sem aviso, e com o
    recorte devolvendo exatamente as mesmas 1.711 linhas de antes, o que
    disfarçou o problema.

    `vocab.normaliza_tipo` já existia e resolve caixa, acento e texto livre
    (`'Apartamento 2 dormitórios (1 suíte)'` -> `apartamento`). O conjunto
    literal fica só como recurso para o que ela não reconhecer.
    """
    canonico = vocab.normaliza_tipo(tipo)
    if canonico:
        return _FAMILIA_CANONICA.get(canonico, "outro")
    # Sobra do desenho antigo: cobre rótulo que o vocabulário ainda não conhece.
    if tipo in RESIDENCIAL:
        return "residencial"
    if tipo in CASA:
        return "casa"
    if tipo in COMERCIAL:
        return "comercial"
    if "Terreno" in str(tipo) or "Area" in str(tipo):
        return "terreno"
    return "outro"


def carrega(base, data: str) -> pd.DataFrame:
    """ABT + validação juntas. A separação de holdout é feita por quem chama."""
    abt = pd.read_parquet(base / f"P03_{data}.parquet")
    val = pd.read_parquet(base / f"P03_validation_{data}.parquet")
    abt["_holdout"] = False
    val["_holdout"] = True
    d = pd.concat([abt, val], ignore_index=True)
    d["familia"] = d.property_type.map(familia)
    return d


def segmenta(d: pd.DataFrame, nome: str) -> pd.DataFrame:
    """Recortes do §2. Nomes são chaves de experimento, não rótulos livres."""
    s = d
    if nome.startswith("venda"):
        s = s[s.transaction_type == "venda"]
    elif nome.startswith("locacao"):
        s = s[s.transaction_type == "locacao"]
    if "_ate800k" in nome:
        s = s[s.price <= 800_000]
    elif "_acima800k" in nome:
        s = s[s.price > 800_000]
    for f in ("residencial", "casa", "comercial"):
        if nome.endswith("_" + f):
            s = s[s.familia == f]
    return s.copy()


SEGMENTOS = ["venda_ate800k", "venda_acima800k", "locacao",
             "venda_ate800k_residencial", "venda_acima800k_residencial", "global"]


# --------------------------------------------------------------------------
# Feature engineering que NÃO depende do alvo -- pode rodar antes do split
# --------------------------------------------------------------------------

def basicas(d: pd.DataFrame) -> pd.DataFrame:
    """
    Razões, ausência como sinal, e transformações de área.

    `n_campos_ausentes` existe porque medimos que linha sem área erra 21,1%
    contra 14,6% das com área: não ter o dado é sinal, não só buraco.
    """
    x = d.copy()
    q = x.bedrooms.replace(0, np.nan)
    x["area_por_quarto"] = x.area_m2 / q
    x["banhos_por_quarto"] = x.bathrooms / q
    x["suites_por_quarto"] = x.suites / q
    x["vagas_por_quarto"] = x.parking_spots / q
    x["comodos"] = x[["bedrooms", "bathrooms"]].sum(axis=1, min_count=1)
    x["area_por_comodo"] = x.area_m2 / x.comodos.replace(0, np.nan)
    x["log_area"] = np.log(x.area_m2.where(x.area_m2 > 0))
    x["condo_por_m2"] = x.condo_fee / x.area_m2.replace(0, np.nan)
    x["iptu_por_m2"] = x.iptu_tax / x.area_m2.replace(0, np.nan)

    for c in CHAVE:
        x[f"aus_{c}"] = x[c].isna()
    x["n_campos_ausentes"] = x[[f"aus_{c}" for c in CHAVE]].sum(axis=1)

    amen = [c for c in x.columns if c.startswith("amen_")]
    nega = [c for c in x.columns if c.startswith("nega_")]
    if amen:
        x["n_amenidades"] = x[amen].sum(axis=1)
    if nega:
        x["n_negacoes"] = x[nega].sum(axis=1)
    return x


# --------------------------------------------------------------------------
# Feature engineering que DEPENDE do alvo -- só dentro do fold de treino
# --------------------------------------------------------------------------

class Encoder:
    """
    Target encoding com suavização bayesiana, ajustado SÓ no treino.

    `(n * media_grupo + k * media_global) / (n + k)`. Com k=20, um bairro de 3
    anúncios fica perto da média global e um de 300 fica perto da sua própria --
    que é o comportamento correto quando 156 dos 183 bairros têm menos de 10
    linhas.

    Codifica log(R$/m²) e não log(preço): o preço do bairro confunde nível com
    tamanho do imóvel, e é a densidade de valor que caracteriza a localização.
    """

    def __init__(self, chaves=("neighborhood", "cep_prefix", "property_type"),
                 k: float = 20.0):
        self.chaves = chaves
        self.k = k
        self.mapas: dict = {}
        self.global_: float = 0.0
        self.contagens: dict = {}

    def ajusta(self, d: pd.DataFrame):
        alvo = np.log((d.price / d.area_m2).replace([np.inf, -np.inf], np.nan))
        ok = alvo.notna()
        self.global_ = float(alvo[ok].mean())
        for chave in self.chaves:
            if chave not in d.columns:
                continue
            g = alvo[ok].groupby(d.loc[ok, chave].astype(str))
            soma, n = g.sum(), g.size()
            self.mapas[chave] = ((soma + self.k * self.global_) / (n + self.k)).to_dict()
            self.contagens[chave] = n.to_dict()
        return self

    def aplica(self, d: pd.DataFrame) -> pd.DataFrame:
        x = d.copy()
        for chave, mapa in self.mapas.items():
            col = x[chave].astype(str)
            x[f"te_{chave}"] = col.map(mapa).fillna(self.global_)
            x[f"n_{chave}"] = col.map(self.contagens[chave]).fillna(0)
        return x


class Imputador:
    """
    Estima `iptu_tax` e `condo_fee` quando o usuário não os informa.

    POR QUE EXISTE. `iptu_tax` tem o maior IV da base (2,521 nas 3.000 do
    VivaReal, acima de área e banheiros) porque é calculado sobre o valor
    venal -- é quase um proxy do preço. Mas os dois campos são OPCIONAIS para
    o usuário por decisão de produto, e o modelo treinado com eles presentes
    aprende a depender do que some na hora de prever.

    Medido em 2026-08-31, n=2.845, 25 folds, cenário `sem_opcionais`:

        treino completo, predição sem os campos    RMSE 97.320
        com imputação                              RMSE 85.273   -12.047, 25/25

    E o custo quando o usuário INFORMA os dois é R$ 52 -- ruído. Descartar as
    colunas também fecha a lacuna (84.565) mas custa R$ 5.335 nesse regime:
    resolve o problema errado, punindo quem tem o dado.

    AJUSTADO SÓ NO TREINO, como o `Encoder`. Um imputador ajustado no conjunto
    inteiro carrega a distribuição do teste para dentro do modelo.

    As bandeiras `aus_iptu_tax` / `aus_condo_fee` continuam dizendo à árvore
    qual valor é medido e qual é estimado -- elas são calculadas em `basicas()`,
    antes da imputação, e não podem ser recalculadas depois.
    """

    CAMPOS = ("iptu_tax", "condo_fee")
    DERIVADAS = DERIVADAS          # fonte única, no topo do módulo

    def __init__(self, n_arvores: int = 400, minimo: int = 60):
        self.n_arvores = n_arvores
        self.minimo = minimo          # abaixo disso não há o que aprender
        self.modelos: dict = {}
        self.colunas: list[str] = []
        self.categorias: dict = {}

    def _fora(self) -> set:
        """Os próprios opcionais e tudo que deriva deles."""
        return set(self.CAMPOS) | {
            der for campo in self.CAMPOS for der in self.DERIVADAS.get(campo, ())}

    def _entrada(self, d: pd.DataFrame) -> pd.DataFrame:
        """
        Preditores da imputação: tudo menos os próprios opcionais.

        As categóricas usam o dtype fixado no ajuste. Sem isso o LightGBM
        recebe `object` e levanta -- e, pior, se cada chamada inferisse as
        próprias categorias, treino e predição codificariam o mesmo bairro com
        inteiros diferentes, sem erro nenhum.
        """
        fora = self._fora()
        x = d[[c for c in self.colunas if c not in fora]].copy()
        for col, dtype in self.categorias.items():
            if col in x.columns:
                x[col] = x[col].astype(str).where(x[col].notna()).astype(dtype)
        for col in x.columns:
            if x[col].dtype == bool:
                x[col] = x[col].astype(np.int8)
        return x

    def ajusta(self, treino: pd.DataFrame):
        import lightgbm as lgb

        self.colunas = [c for c in matriz(treino).columns]
        fora = self._fora()
        self.categorias = {
            c: pd.CategoricalDtype(sorted(treino[c].dropna().astype(str).unique()))
            for c in self.colunas
            if c not in fora and treino[c].dtype == object
        }
        params = dict(objective="regression", metric="rmse", learning_rate=0.05,
                      num_leaves=15, min_data_in_leaf=30, feature_fraction=0.8,
                      bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
                      verbosity=-1)
        for campo in self.CAMPOS:
            if campo not in treino.columns:
                continue
            y = treino[campo]
            ok = y.notna() & (y > 0)
            if int(ok.sum()) < self.minimo:
                continue
            X = self._entrada(treino[ok])
            # log: as duas caudas são longas e o erro relativo é o que importa.
            self.modelos[campo] = lgb.train(
                params, lgb.Dataset(X, np.log(y[ok])), self.n_arvores)
        return self

    def aplica(self, d: pd.DataFrame) -> pd.DataFrame:
        x = d.copy()
        for campo, modelo in self.modelos.items():
            falta = x[campo].isna()
            if not falta.any():
                continue
            x.loc[falta, campo] = np.exp(modelo.predict(self._entrada(x[falta])))
        # As derivadas TÊM de ser refeitas do valor imputado: mantê-las nulas
        # deixaria o modelo com meia informação e um buraco onde havia sinal.
        if "area_m2" in x.columns:
            area = x.area_m2.replace(0, np.nan)
            for campo in self.CAMPOS:
                for derivada in self.DERIVADAS.get(campo, ()):
                    if derivada in x.columns:
                        x[derivada] = x[campo] / area
        return x


def matriz(d: pd.DataFrame) -> pd.DataFrame:
    return d[[c for c in d.columns if c not in NAO_PREDITOR and not c.startswith("_")]]
