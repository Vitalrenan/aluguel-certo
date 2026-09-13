"""
The market table -- the single table the API reads.

    python -m refino.mercado                  # relata, nao grava
    python -m refino.mercado --write

    03_refined/mercado/mercado_{aaaa-mm}.parquet

GRAIN: one row per city, neighbourhood and reference month. City-level columns
repeat on every neighbourhood row. The denormalisation is deliberate -- the API
reads and returns, with no join and no pointer to a most-recent file. Four reads
across three formats become one.

FIVE BLOCKS, FOUR SOURCES:

    identificacao   cidade, uf, bairro, mes_referencia
    geografia       lat, lon, cep_prefixo                    CNEFE
    nosso mercado   n_anuncios, preco_mediano, preco_m2...   listings tratada
    indice          fipezap_*                                FipeZAP
    macro           ipca_12m, recorte_ipca                   SIDRA

GEOCODING, TWO ROUTES, AND NEITHER INVENTS A POINT:

  1. The listing carries a CEP, and `cep_coord` gives lat/lon.
  2. The listing carries an address, and `logradouro_cep` maps municipality plus
     canonical street to a CEP, which route 1 then resolves.

Both use the SAME `chave` from `comum.logradouro` as the table was built with.
Two different normalisations would match almost nothing and nobody would notice.

A NEIGHBOURHOOD WITH NO COORDINATE KEEPS `lat` NULL. It does not get the city
centroid, nor a neighbour's. An invented point on a map is indistinguishable
from a measured one, and the map is the part of the screen a user trusts most
without checking.

THE CENTROID IS THE MEDIAN, not the mean: one wrong CEP drags the mean into
another municipality and the median does not move.

`recorte_ipca` READS `Brasil` ON EVERY ROW. The index is national and this table
puts it on neighbourhood rows, where it looks local. The column is what stops
that reading downstream.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from comum.logradouro import chave, lugar

LISTINGS_PADRAO = "dados/02_processed/listings_enriquecidos"
CNEFE_PADRAO = "dados/02_processed/cnefe"
FIPEZAP_PADRAO = "dados/02_processed/fipezap/serie_mensal.parquet"
SIDRA_PADRAO = "dados/02_processed/sidra/serie_mensal.parquet"
SAIDA_PADRAO = "dados/03_refined/mercado"


class MercadoVazio(Exception):
    """Zero rows is a failure, never a success."""


def le_listings(raiz: str | Path) -> pd.DataFrame:
    arquivos = sorted(Path(raiz).glob("*/*/ano=*/mes=*/*.parquet"))
    if not arquivos:
        return pd.DataFrame()
    partes = [pd.read_parquet(a) for a in arquivos]
    colunas = sorted(set().union(*(p.columns for p in partes)))
    return pd.concat([p.reindex(columns=colunas) for p in partes],
                     ignore_index=True)


def geocodifica(d: pd.DataFrame, cnefe: str | Path) -> pd.Series:
    """
    A CEP per listing, by the two routes, in order. Never a guessed one.

    Returns the CEP; turning it into a point is the caller's step, so the two
    routes cannot disagree about how a coordinate is looked up.
    """
    cep = (d["cep"].astype(str).str.replace(r"\D", "", regex=True)
           if "cep" in d else pd.Series("", index=d.index))
    cep = cep.where(cep.str.len() == 8, "")

    caminho = Path(cnefe) / "logradouro_cep.parquet"
    if not caminho.exists() or "address" not in d:
        return cep

    ruas = pd.read_parquet(caminho)
    ruas["municipio_k"] = ruas.municipio.map(chave)
    mapa = dict(zip(zip(ruas.municipio_k, ruas.chave), ruas.cep))

    faltam = cep == ""
    if not faltam.any():
        return cep
    chaves = list(zip(d.loc[faltam, "city"].map(chave),
                      d.loc[faltam, "address"].map(chave)))
    cep.loc[faltam] = [mapa.get(k, "") for k in chaves]
    return cep


def _junta_atrasado(esquerda: pd.DataFrame, direita: pd.DataFrame,
                    por: list[str] | None, sufixo: str = "indice") -> pd.DataFrame:
    """
    Join an index published with a lag: the most recent value up to the month.

    EXACT MATCH IS THE WRONG RULE HERE, and it fails quietly. FipeZAP publishes
    the yield to 2026-07 and the price to 2026-08; the IPCA lands a month after
    the month it describes. Requiring `mes_referencia` to match exactly left
    every September row null and the report said "0% com rentabilidade" -- which
    reads as a broken join rather than as a publication calendar.

    `merge_asof` takes the latest row at or before each month, so a September
    listing carries July's yield and says so through `mes_referencia_indice`.
    """
    # `merge_asof` exige chave ordenavel numerica, e `2026-08` e texto. Vira
    # 202608, que ordena igual e nao inventa um dia que a fonte nao publica.
    def _num(s):
        return s.str.replace("-", "", regex=False).astype(int)

    # O NOME DA COLUNA DE MÊS CARREGA A FONTE. Chamar as duas de
    # `mes_referencia_indice` fazia o pandas desempatar sozinho com `_x` e `_y`
    # -- sufixo que não diz qual é da FipeZAP e qual é do IPCA, e que muda de
    # lado se a ordem das junções mudar.
    coluna_mes = f"mes_referencia_{sufixo}"

    e = esquerda.copy()
    e["_m"] = _num(e.mes_referencia)
    d = direita.rename(columns={"mes_referencia": coluna_mes}).copy()
    d["_m"] = _num(d[coluna_mes])

    e = e.sort_values("_m")
    d = d.sort_values("_m")

    saida = pd.merge_asof(e, d, on="_m", by=por, direction="backward")
    return saida.drop(columns=["_m"])


def constroi(listings: pd.DataFrame, cnefe: str | Path,
             fipezap: str | Path, sidra: str | Path) -> tuple[pd.DataFrame, dict]:
    d = listings.copy()
    d["cep_resolvido"] = geocodifica(d, cnefe)
    d["cep_prefixo"] = d.cep_resolvido.str[:5].replace("", None)

    # ponto por anuncio, via CEP
    caminho = Path(cnefe) / "cep_coord.parquet"
    if caminho.exists():
        coord = pd.read_parquet(caminho).drop_duplicates(subset="cep")
        pontos = coord.set_index("cep")[["lat", "lon"]]
        d = d.join(pontos, on="cep_resolvido")
    else:
        d["lat"] = d["lon"] = None

    chaves = ["city", "state", "neighborhood", "mes_referencia"]
    faltando = [c for c in chaves if c not in d.columns]
    if faltando:
        raise MercadoVazio(f"colunas de grao ausentes: {faltando}")

    # NORMALIZA ANTES DE AGRUPAR, com a mesma funcao que a ABT usa. Sem isto
    # `Guaruja` e `Guaruja` com acento viram dois bairros do mesmo lugar, cada
    # um com metade dos anuncios e uma mediana que nao descreve nenhum deles.
    d["city"] = d.city.map(lugar)
    d["neighborhood"] = d.neighborhood.map(lugar)
    d["state"] = d.state.map(lambda v: str(v).strip().upper())

    d["preco_m2"] = d.price / d.area_m2

    g = d.groupby(chaves, dropna=False)
    tabela = g.agg(
        n_anuncios=("price", "size"),
        preco_mediano=("price", "median"),
        preco_p10=("price", lambda s: s.quantile(0.10)),
        preco_p90=("price", lambda s: s.quantile(0.90)),
        preco_m2_mediano=("preco_m2", "median"),
        # MEDIANA, nunca media: um CEP errado leva a media para outro
        # municipio e a mediana nao se move.
        lat=("lat", "median"),
        lon=("lon", "median"),
        cep_prefixo=("cep_prefixo", lambda s: s.mode().iloc[0] if s.notna().any() else None),
    ).reset_index()
    tabela = tabela.rename(columns={"city": "cidade", "state": "uf",
                                    "neighborhood": "bairro"})

    relato = {"bairros": len(tabela),
              "com_coordenada": int(tabela.lat.notna().sum())}

    # --- indice publicado, por cidade e mes ------------------------------
    if Path(fipezap).exists():
        fz = pd.read_parquet(fipezap)[
            ["cidade", "mes_referencia", "venda_m2", "venda_var12",
             "locacao_m2", "locacao_var12",
             "yield_mensal", "yield_p10", "yield_p90"]]
        fz = fz.rename(columns={
            "venda_m2": "fipezap_venda_m2", "venda_var12": "fipezap_venda_var12",
            "locacao_m2": "fipezap_locacao_m2",
            "locacao_var12": "fipezap_locacao_var12",
            "yield_mensal": "fipezap_yield_mensal",
            "yield_p10": "fipezap_yield_p10", "yield_p90": "fipezap_yield_p90"})
        # A juncao usa a chave canonica: `Sao Paulo` da base e `São Paulo` da
        # planilha sao a mesma cidade e a comparacao literal casaria zero.
        # PROPAGA O ULTIMO VALOR PUBLICADO DE CADA CAMPO, por cidade.
        #
        # Os campos nao terminam no mesmo mes: a FipeZAP publica preco ate
        # 2026-08 e rentabilidade ate 2026-07. Sem isto o `asof` casa a LINHA
        # de agosto -- que existe, com rentabilidade nula -- e a cobertura sai
        # 0%, o que se le como juncao quebrada em vez de calendario de
        # publicacao. O preenchimento e para frente apenas: nenhum mes recebe
        # valor de um mes futuro.
        campos = [c for c in fz.columns
                  if c.startswith("fipezap_")]
        fz = fz.sort_values(["cidade", "mes_referencia"])
        fz[campos] = fz.groupby("cidade")[campos].ffill()

        fz["_k"] = fz.cidade.map(chave)
        tabela["_k"] = tabela.cidade.map(chave)
        tabela = _junta_atrasado(tabela, fz.drop(columns=["cidade"]),
                                 por=["_k"], sufixo="fipezap")
        relato["com_fipezap"] = int(tabela.fipezap_venda_m2.notna().sum())
        relato["com_yield"] = int(tabela.fipezap_yield_mensal.notna().sum())
        tabela = tabela.drop(columns=["_k"])

    # --- macro, por mes ---------------------------------------------------
    if Path(sidra).exists():
        sd = pd.read_parquet(sidra)[["recorte", "mes_referencia", "ipca_12m"]]
        sd = sd.rename(columns={"recorte": "recorte_ipca"})
        tabela = _junta_atrasado(tabela, sd, por=None, sufixo="ipca")
        relato["com_ipca"] = int(tabela.ipca_12m.notna().sum())

    # AS CIDADES DA FIPEZAP QUE NÃO TÊM ANÚNCIO NOSSO ENTRAM MESMO ASSIM, como
    # linha de cidade com `bairro` nulo.
    #
    # O grão da tabela é cidade × bairro × mês e nasce dos nossos anúncios, o
    # que restringia a resposta às 6 cidades onde coletamos. O mapa do front
    # acompanha 15 capitais e ficou com 14 pinos tracejados -- a FipeZAP publica
    # todas as 15, e a informação existia; era o grão que a escondia.
    #
    # A linha de cidade traz só o bloco de índice. As colunas do nosso mercado
    # ficam nulas de propósito: nulo é "não medimos aqui", e preencher com zero
    # faria a tela mostrar uma cidade com zero imóveis como se fosse medida.
    if Path(fipezap).exists():
        fz_todas = pd.read_parquet(fipezap)
        mes_alvo = tabela.mes_referencia.max()
        ja_temos = {chave(c) for c in tabela.cidade}

        faltantes = (fz_todas[~fz_todas.cidade.map(chave).isin(ja_temos)]
                     .sort_values("mes_referencia")
                     .drop_duplicates(subset="cidade", keep="last"))
        if len(faltantes):
            extra = pd.DataFrame({
                "cidade": faltantes.cidade.map(lugar).values,
                "uf": None, "bairro": None, "mes_referencia": mes_alvo,
                "n_anuncios": 0,
                "fipezap_venda_m2": faltantes.venda_m2.values,
                "fipezap_venda_var12": faltantes.venda_var12.values,
                "fipezap_locacao_m2": faltantes.locacao_m2.values,
                "fipezap_locacao_var12": faltantes.locacao_var12.values,
                "fipezap_yield_mensal": faltantes.yield_mensal.values,
                "fipezap_yield_p10": faltantes.yield_p10.values,
                "fipezap_yield_p90": faltantes.yield_p90.values,
                "mes_referencia_fipezap": faltantes.mes_referencia.values,
            })
            if "ipca_12m" in tabela:
                extra["ipca_12m"] = tabela.ipca_12m.dropna().iloc[0] \
                    if tabela.ipca_12m.notna().any() else None
                extra["recorte_ipca"] = "Brasil"
            tabela = pd.concat([tabela, extra], ignore_index=True)
            relato["cidades_so_indice"] = len(extra)

    return tabela.sort_values(["cidade", "bairro", "mes_referencia"]
                              ).reset_index(drop=True), relato


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--listings", default=LISTINGS_PADRAO)
    ap.add_argument("--cnefe", default=CNEFE_PADRAO)
    ap.add_argument("--fipezap", default=FIPEZAP_PADRAO)
    ap.add_argument("--sidra", default=SIDRA_PADRAO)
    ap.add_argument("--saida", default=SAIDA_PADRAO)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    listings = le_listings(args.listings)
    if not len(listings):
        print(f"nenhuma tabela tratada em {args.listings}", file=sys.stderr)
        return 1

    tabela, relato = constroi(listings, args.cnefe, args.fipezap, args.sidra)
    if not len(tabela):
        raise MercadoVazio("tabela vazia a partir de entrada nao vazia")

    print("=" * 72)
    print("TABELA DE MERCADO")
    print("=" * 72)
    print(f"  {len(tabela):,} linhas   grao cidade x bairro x mes")
    print(f"  {tabela.cidade.nunique()} cidades   {tabela.bairro.nunique()} bairros   "
          f"meses {sorted(set(tabela.mes_referencia))}")

    print("\n  cobertura:")
    print(f"    com coordenada        {tabela.lat.notna().mean():>7.1%}"
          f"   ({relato['com_coordenada']} de {relato['bairros']})")
    for col, rot in (("fipezap_venda_m2", "com preco FipeZAP"),
                     ("fipezap_yield_mensal", "com rentabilidade"),
                     ("ipca_12m", "com IPCA")):
        if col in tabela:
            print(f"    {rot:<22}{tabela[col].notna().mean():>7.1%}")

    print("\n  bairros SEM coordenada ficam com lat nula, nunca com centroide:")
    sem = tabela[tabela.lat.isna()]
    print(f"    {len(sem)} linha(s), {sem.bairro.nunique()} bairro(s) distintos")

    print("\n  as 8 cidades com mais anuncios:")
    por_cidade = (tabela.groupby("cidade")
                  .agg(bairros=("bairro", "nunique"),
                       anuncios=("n_anuncios", "sum"),
                       com_coord=("lat", lambda s: s.notna().sum()))
                  .sort_values("anuncios", ascending=False).head(8))
    for cidade, r in por_cidade.iterrows():
        print(f"    {cidade:<16}{int(r.anuncios):>7} anuncios  "
              f"{int(r.bairros):>4} bairros  {int(r.com_coord):>4} com coordenada")

    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0

    destino = Path(args.saida)
    destino.mkdir(parents=True, exist_ok=True)
    escritos = []
    for mes, parte in tabela.groupby("mes_referencia"):
        alvo = destino / f"mercado_{mes}.parquet"
        try:
            parte.to_parquet(alvo, index=False)
        except Exception as exc:
            raise MercadoVazio(f"falha ao gravar {alvo}: {exc}") from exc
        escritos.append((alvo, len(parte)))
    print(f"\n  gravados {len(escritos)} arquivo(s):")
    for caminho, n in escritos:
        print(f"    {caminho}  ({n} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
