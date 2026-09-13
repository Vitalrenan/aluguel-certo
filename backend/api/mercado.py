"""
Reads of the market table.

ONE TABLE, ONE QUERY. The old service assembled these answers from four files
in three formats -- two parquets and two JSON, one of them behind a
`mais_recente.json` pointer -- and each route joined them by hand. The refined
layer exists so that stops.

`recorte_ipca` IS RETURNED, ALWAYS. The index is national and these rows are
neighbourhood rows, so the number looks local by the time it reaches a screen.
Dropping the column here would be the last chance to say otherwise.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from api.resolucao import slug


class MercadoAusente(Exception):
    """The refined layer is not where it should be."""


def _mes_mais_recente(raiz: Path) -> str | None:
    arquivos = sorted((raiz / "mercado").glob("mercado_*.parquet"))
    if not arquivos:
        return None
    return arquivos[-1].stem.replace("mercado_", "")


def carrega(raiz: str | Path, mes: str | None = None) -> pd.DataFrame:
    raiz = Path(raiz)
    alvo_mes = mes or _mes_mais_recente(raiz)
    if alvo_mes is None:
        raise MercadoAusente(
            f"nenhuma tabela de mercado em {raiz / 'mercado'} -- "
            "o estagio de refino nao rodou")
    caminho = raiz / "mercado" / f"mercado_{alvo_mes}.parquet"
    if not caminho.exists():
        raise MercadoAusente(f"{caminho} nao existe")
    return pd.read_parquet(caminho)


def _limpa(d: pd.DataFrame) -> list[dict]:
    """Records with NaN turned into null, which JSON can carry and NaN cannot."""
    return d.astype(object).where(pd.notna(d), None).to_dict("records")


def por_cidade(raiz: str | Path, cidade: str | None = None,
               mes: str | None = None) -> dict:
    d = carrega(raiz, mes)
    if cidade:
        d = d[d.cidade.map(slug) == slug(cidade)]

    colunas_cidade = [c for c in (
        "cidade", "uf", "mes_referencia", "fipezap_venda_m2",
        "fipezap_locacao_m2", "fipezap_yield_mensal", "fipezap_yield_p10",
        "fipezap_yield_p90", "ipca_12m", "recorte_ipca",
        "mes_referencia_indice") if c in d.columns]

    agregado = (d.groupby("cidade", as_index=False)
                .agg(n_bairros=("bairro", "nunique"),
                     n_anuncios=("n_anuncios", "sum"),
                     preco_mediano=("preco_mediano", "median"),
                     preco_m2_mediano=("preco_m2_mediano", "median")))
    indice = d[colunas_cidade].drop_duplicates(subset=["cidade"])
    saida = agregado.merge(indice, on="cidade", how="left")

    return {"mes": mes or _mes_mais_recente(Path(raiz)),
            "n": len(saida), "cidades": _limpa(saida)}


def por_bairro(raiz: str | Path, cidade: str | None = None,
               mes: str | None = None, com_coordenada: bool = False) -> dict:
    d = carrega(raiz, mes)
    if cidade:
        d = d[d.cidade.map(slug) == slug(cidade)]
    total = len(d)
    if com_coordenada:
        d = d[d.lat.notna()]

    colunas = [c for c in (
        "cidade", "uf", "bairro", "mes_referencia", "n_anuncios",
        "preco_mediano", "preco_p10", "preco_p90", "preco_m2_mediano",
        "lat", "lon", "cep_prefixo", "ipca_12m", "recorte_ipca")
        if c in d.columns]

    return {"mes": mes or _mes_mais_recente(Path(raiz)),
            "n": len(d),
            "n_total": total,
            "sem_coordenada": total - int(d.lat.notna().sum()) if not com_coordenada
            else total - len(d),
            "bairros": _limpa(d[colunas].sort_values(
                ["cidade", "n_anuncios"], ascending=[True, False]))}
