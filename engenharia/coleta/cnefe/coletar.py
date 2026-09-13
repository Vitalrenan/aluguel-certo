"""
CNEFE ingestion -- IBGE address register into the raw layer.

    python -m coleta.cnefe.coletar                     # relata, nao grava
    python -m coleta.cnefe.coletar --write
    python -m coleta.cnefe.coletar --municipios 3548500 --write

WHAT THIS IS. Open data, a direct download from the agency's own FTP. Not
scraping: there is no pagination, no search, no listing page, and it does not
go through the politeness layer, which exists for third-party sites rather than
for a public statistics portal.

WHY IT IS A SEPARATE PROCESS. `construir_logradouros.py` downloaded the zip,
read it, AND decided the final shape of the lookup table -- all before anything
reached disk. That is a modelling decision taken at ingestion, which is where it
must not live: the day a second derivation needs a column the first one dropped,
the whole download has to happen again.

Here the raw layer stops at "what the source said, minus what we will never
use", and every derivation reads from it.

THE 9 COLUMNS, OUT OF 34. Two lookups are built downstream -- CEP to coordinate
and street to CEP -- and these are the columns they need:

    COD_MUNICIPIO                             which city
    CEP                                       the join key of both lookups
    DSC_LOCALIDADE                            neighbourhood
    NOM_TIPO_SEGLOGR / _TITULO_ / NOM_SEGLOGR the street name, in three parts
    LATITUDE / LONGITUDE                      the point
    NV_GEO_COORD                              how the point was obtained

WHAT IS DELIBERATELY LEFT BEHIND. `NUM_ENDERECO` is the house number, and
`NOM_COMP_ELEM*` / `VAL_COMP_ELEM*` carry block and apartment. Together they
identify a dwelling. Our use is aggregate -- a CEP gets a coordinate, a street
gets a CEP -- and loading a precision the project never consumes is keeping
granularity without a purpose. It is public data either way; that is a reason
to be deliberate, not a reason to be careless.
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE = ("https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_"
        "Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/")

ANO_CENSO = "2022"
CACHE_PADRAO = "dados/00_external/cnefe"
RAW_PADRAO = "dados/01_raw/cnefe"

CABECALHO = {"User-Agent": "AluguelCerto/0.1 (pesquisa de mercado; dados abertos IBGE)"}

# Codigos conferidos na listagem do proprio IBGE em 2026-09-12, nao de memoria.
#
# A tabela antiga de CEP->coordenada cobria 55.016 CEPs e a reconstrucao so com
# Santos e Sao Paulo deu 48.258. Os 6.758 que faltavam eram destes municipios:
# a Baixada inteira aparece na base de anuncios, e a geocodificacao por CEP
# depende de o municipio estar aqui. Nos 48.258 CEPs comuns a reconstrucao bate
# exato -- lat, lon e contagem identicos -- entao a diferenca era cobertura, e
# nao metodo.
MUNICIPIOS = {
    "3548500": ("35_SP", "3548500_SANTOS.zip", "Santos"),
    "3550308": ("35_SP", "3550308_SAO_PAULO.zip", "Sao Paulo"),
    "3551009": ("35_SP", "3551009_SAO_VICENTE.zip", "Sao Vicente"),
    "3518701": ("35_SP", "3518701_GUARUJA.zip", "Guaruja"),
    "3541000": ("35_SP", "3541000_PRAIA_GRANDE.zip", "Praia Grande"),
    "3513504": ("35_SP", "3513504_CUBATAO.zip", "Cubatao"),
    "3506359": ("35_SP", "3506359_BERTIOGA.zip", "Bertioga"),
}

COLUNAS = ["COD_MUNICIPIO", "CEP", "DSC_LOCALIDADE",
           "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR",
           "LATITUDE", "LONGITUDE", "NV_GEO_COORD"]

# Sem estas duas nao ha tabela nenhuma. As outras sete podem faltar num
# municipio e a derivacao ainda sai, mais pobre e com a falta relatada.
OBRIGATORIAS = ["CEP", "NOM_SEGLOGR"]


class LayoutInesperado(Exception):
    """The CSV does not carry the columns both lookups need."""


def baixa(codigo: str, cache: Path) -> Path:
    """Fetch the municipality zip, or reuse the cached one."""
    uf, arquivo, nome = MUNICIPIOS[codigo]
    alvo = cache / arquivo
    if alvo.exists():
        print(f"  {nome:<12} ja em disco ({alvo.stat().st_size / 1e6:.1f} MB)")
        return alvo
    url = f"{BASE}{uf}/{arquivo}"
    print(f"  {nome:<12} baixando {arquivo} ...", flush=True)
    cache.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=CABECALHO, timeout=900, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        lido = 0
        with alvo.open("wb") as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
                lido += len(bloco)
                if total and lido % (20 << 20) < (1 << 20):
                    print(f"      {lido / 1e6:>7.0f} / {total / 1e6:.0f} MB",
                          flush=True)
    print(f"  {nome:<12} {alvo.stat().st_size / 1e6:.1f} MB")
    return alvo


def le(caminho: Path) -> pd.DataFrame:
    """The zipped CSV, every column as text."""
    with zipfile.ZipFile(caminho) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not nomes:
            raise LayoutInesperado(f"{caminho} nao contem CSV")
        with z.open(nomes[0]) as f:
            return pd.read_csv(io.TextIOWrapper(f, "latin-1"), sep=";",
                               dtype=str, low_memory=False)


def projeta(d: pd.DataFrame) -> pd.DataFrame:
    """
    The nine columns, named rather than guessed at.

    Naming them matters. An earlier version matched columns by substring and
    picked up `NOM_TITULO_SEGLOGR` -- which holds only the title (`PRESIDENTE`,
    `DOUTOR`), 27 distinct values. The table came out with 127 streets from
    231.082 addresses, and nothing raised.
    """
    cols = {c.upper(): c for c in d.columns}
    faltando = [c for c in OBRIGATORIAS if c not in cols]
    if faltando:
        raise LayoutInesperado(
            f"faltam as colunas obrigatorias {faltando}. "
            f"Presentes: {sorted(cols)[:12]}")

    saida = {}
    for nome in COLUNAS:
        saida[nome.lower()] = d[cols[nome]] if nome in cols else None
    projetado = pd.DataFrame(saida)
    projetado["cep"] = projetado["cep"].str.replace(r"\D", "", regex=True)
    return projetado


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--municipios", nargs="*", default=list(MUNICIPIOS))
    ap.add_argument("--cache", default=CACHE_PADRAO)
    ap.add_argument("--raw-dir", default=RAW_PADRAO)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    escritos, total = [], 0
    for cod in args.municipios:
        if cod not in MUNICIPIOS:
            print(f"  municipio {cod} nao mapeado -- pulando")
            continue
        bruto = le(baixa(cod, Path(args.cache)))
        print(f"      {len(bruto):,} enderecos, {bruto.shape[1]} colunas na fonte")

        d = projeta(bruto)
        # Zero linhas e falha, nunca sucesso.
        if not len(d):
            print(f"  {cod}: 0 linhas apos a projecao", file=sys.stderr)
            return 1

        print(f"      {len(d):,} linhas x {d.shape[1]} colunas carregadas "
              f"({bruto.shape[1] - d.shape[1]} colunas deixadas de fora)")
        for c in d.columns:
            taxa = d[c].notna().mean()
            if taxa < 1.0:
                print(f"        {c:<22}{taxa:>7.1%}")
        if "nv_geo_coord" in d:
            print("      nivel de geocodificacao:")
            for nivel, n in d.nv_geo_coord.value_counts().sort_index().items():
                print(f"        {nivel}  {n:>8,}  {n / len(d):>6.1%}")
        total += len(d)

        if args.write:
            pasta = Path(args.raw_dir) / cod / ANO_CENSO
            pasta.mkdir(parents=True, exist_ok=True)
            alvo = pasta / "enderecos.parquet"
            try:
                d.to_parquet(alvo, index=False)
            except Exception as exc:
                # Falha de escrita levanta. Nunca engolir excecao de
                # persistencia: uma escrita falha que retorna em silencio e
                # indistinguivel de uma bem-sucedida.
                raise RuntimeError(f"falha ao gravar {alvo}: {exc}") from exc
            escritos.append(alvo)

    if not total:
        print("nenhum municipio processado", file=sys.stderr)
        return 1
    if not args.write:
        print("\nsem --write: nada foi gravado.")
        return 0
    print(f"\n  gravados {len(escritos)} arquivo(s), {total:,} linha(s):")
    for c in escritos:
        print(f"    {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
