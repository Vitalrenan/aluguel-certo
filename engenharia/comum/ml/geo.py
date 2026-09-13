"""
CEP -> latitude/longitude, do CNEFE 2022 do IBGE.

Fonte: ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/
       Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/35_SP/
Base publica e offline -- nenhum CEP nosso e enviado a servico externo.

Duas outras pastas do CNEFE NAO servem, e a confusao custa tempo:
  Agregados_por_CEP/     tem CEP, nao tem coordenada
  Coordenadas_enderecos/ tem coordenada, nao tem CEP
So `Arquivos_CNEFE` traz os dois no mesmo registro.

Precisao verificada em 2026-08-30 contra fonte independente, n=12 CEPs de
Santos: mediana de 0,08 km de diferenca, maxima de 1,34 km. Cobertura: 586 dos
624 CEPs da nossa base (93,9%).

NORMALIZACAO -- decisao de 2026-08-30.
Min-max POR CIDADE, nao global. Arvore e invariante a escala monotona, entao
dentro de uma cidade isto nao muda nada: o modelo treinado com coordenada crua
e com normalizada e o mesmo. O ganho e na TRANSFERENCIA entre cidades -- a
coordenada vira posicao relativa dentro do municipio, e um corte aprendido em
Santos passa a ter algum sentido em Guaruja. Normalizacao global nao faria
isso: manteria Santos e Sao Paulo em cantos opostos do intervalo.

Nenhuma feature de distancia e construida, por decisao explicita: o modelo
deve aprender o padrao de valorizacao sozinho, para que a abordagem sirva a
cidades cuja geografia nao conhecemos.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TABELA = (Path(__file__).resolve().parent.parent.parent
          / "data/02_processed/referencia/cep_coord_cnefe2022.parquet")


def carrega_tabela() -> pd.DataFrame:
    d = pd.read_parquet(TABELA)
    d["CEP"] = d.CEP.astype(str).str.zfill(8)
    return d.set_index("CEP")


def junta(d: pd.DataFrame, tabela: pd.DataFrame | None = None) -> pd.DataFrame:
    """Acrescenta cep_lat/cep_lon e a versao normalizada por cidade."""
    t = carrega_tabela() if tabela is None else tabela
    x = d.copy()
    chave = x.cep.astype(str).str.replace(r"\D", "", regex=True).str.zfill(8)
    x["cep_lat"] = chave.map(t.lat)
    x["cep_lon"] = chave.map(t.lon)
    # `n_end` = quantos enderecos o IBGE conta naquele CEP. Proxy de densidade
    # construida, e vem de graca no mesmo join.
    x["cep_n_enderecos"] = chave.map(t.n_end)

    # Min-max por cidade. Cidade com um unico ponto teria divisao por zero;
    # nesse caso o valor normalizado e 0.5 (centro do intervalo), que e o
    # unico valor sem informacao mas tambem sem vies.
    for eixo in ("lat", "lon"):
        col = f"cep_{eixo}"
        g = x.groupby("city")[col]
        lo, hi = g.transform("min"), g.transform("max")
        faixa = (hi - lo).replace(0, np.nan)
        x[f"{col}_mm"] = ((x[col] - lo) / faixa).fillna(0.5).where(x[col].notna())
    return x.drop(columns=["cep_lat", "cep_lon"])


# --------------------------------------------------------------------------
# Features espaciais — §3 do plano, medidas em 2026-09-05
# --------------------------------------------------------------------------
#
# POR QUE EXISTEM. Uma árvore só corta PARALELO AOS EIXOS. Aprender "perto da
# praia" ou "quarteirão valorizado" exige aproximar uma fronteira inclinada por
# uma escada de retângulos -- caro, e com pouca coordenada não há dado para
# isso. Estas features dão a combinação dos eixos que a árvore não constrói.
#
# Trocar o NORMALIZADOR não resolveria: min-max, z-score e quantil produzem o
# mesmo modelo, porque a árvore escolhe corte por ordenação. Qualquer
# transformação monótona de uma variável isolada é no-op.
#
# GANHO MEDIDO (25 folds, alvo deduplicado, cenário sem opcionais):
#
#                              Santos            São Paulo
#     eixos rotacionados        +8  p=0,69      -728  p=0,0051
#     interações               -60  p=0,67    -1.146  p=0,0028
#     alvo espacial (kNN)      -97  p=0,54    -1.638  p=0,0001
#     as três juntas          -314  p=0,003   -2.109  p=0,0000  (21/25)
#
# A diferença entre cidades é o resultado, não ruído. Em Santos o bairro já
# codifica a geografia -- "Gonzaga", "Ponta da Praia" dizem onde é o mar, e a
# coordenada é redundante (IV 0,080 contra 0,226 do bairro). Em São Paulo os
# bairros são grandes e heterogêneos, e a coordenada acrescenta resolução que
# o nome não tem.


def interacoes(d: pd.DataFrame) -> pd.DataFrame:
    """
    Combinações dos dois eixos. Sem ajuste, sem alvo -- pode rodar em qualquer
    lugar do pipeline.

    `geo_dif` (lat - lon) é a que mais importa numa cidade de eixo diagonal:
    ela vira uma coordenada ao longo da faixa, e um único corte passa a separar
    o que antes exigia uma escada.
    """
    x = d.copy()
    la, lo = x.get("cep_lat_mm"), x.get("cep_lon_mm")
    if la is None or lo is None:
        return x
    x["geo_prod"] = la * lo
    x["geo_soma"] = la + lo
    x["geo_dif"] = la - lo
    x["geo_raio"] = np.sqrt((la - 0.5) ** 2 + (lo - 0.5) ** 2)
    return x


class Rotacao:
    """
    Eixos principais da nuvem de coordenadas (PCA), ajustados no TREINO.

    Não usa o alvo, então não é vazamento no sentido do `Encoder` -- mas é
    ajustado no treino mesmo assim: a nuvem do teste não deve influenciar a
    orientação dos eixos que o modelo aprendeu a cortar.
    """

    EIXOS = ["cep_lat_mm", "cep_lon_mm"]

    def __init__(self):
        self.pca = None

    def ajusta(self, treino: pd.DataFrame):
        from sklearn.decomposition import PCA
        ok = treino[self.EIXOS].notna().all(axis=1)
        if int(ok.sum()) > 50:
            self.pca = PCA(n_components=2).fit(treino.loc[ok, self.EIXOS].values)
        return self

    def aplica(self, d: pd.DataFrame) -> pd.DataFrame:
        x = d.copy()
        if self.pca is None:
            return x
        m = x[self.EIXOS].notna().all(axis=1)
        z = np.full((len(x), 2), np.nan)
        if m.any():
            z[m.values] = self.pca.transform(x.loc[m, self.EIXOS].values)
        x["geo_pc1"], x["geo_pc2"] = z[:, 0], z[:, 1]
        return x


class VizinhancaAlvo:
    """
    Preço mediano por m² dos K vizinhos mais próximos em coordenada.

    É o que responde "este ponto do mapa é caro?" sem que ninguém diga onde
    fica a praia, o parque ou o bairro nobre. O modelo descobre pelo preço dos
    vizinhos.

    USA O ALVO, então segue a mesma disciplina do `Encoder`: ajustado só no
    fold de treino, e no treino o PRÓPRIO PONTO é excluído da sua vizinhança.
    Sem essa exclusão a feature carrega o alvo da própria linha e o ganho vira
    memória em vez de previsão.
    """

    EIXOS = ["cep_lat_mm", "cep_lon_mm"]

    def __init__(self, k: int = 15):
        self.k = k
        self.nn = None
        self.valores = None

    def ajusta(self, treino: pd.DataFrame):
        from sklearn.neighbors import NearestNeighbors
        ok = treino[self.EIXOS].notna().all(axis=1)
        if int(ok.sum()) <= self.k + 5:
            return self
        alvo = np.log((treino.loc[ok, "price"] / treino.loc[ok, "area_m2"]))
        bom = np.isfinite(alvo.values)
        pts = treino.loc[ok, self.EIXOS].values[bom]
        if len(pts) <= self.k + 1:
            return self
        self.valores = alvo.values[bom]
        self.nn = NearestNeighbors(
            n_neighbors=min(self.k + 1, len(pts))).fit(pts)
        return self

    def aplica(self, d: pd.DataFrame, treino: bool = False) -> pd.DataFrame:
        x = d.copy()
        if self.nn is None:
            x["geo_knn_ppm2"] = np.nan
            return x
        m = x[self.EIXOS].notna().all(axis=1)
        v = np.full(len(x), np.nan)
        if m.any():
            _, idx = self.nn.kneighbors(x.loc[m, self.EIXOS].values)
            # No treino o vizinho 0 e a propria linha.
            idx = idx[:, 1:] if treino else idx[:, :min(self.k, idx.shape[1])]
            v[m.values] = self.valores[idx].mean(axis=1)
        x["geo_knn_ppm2"] = v
        return x
