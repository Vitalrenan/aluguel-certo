"""
Inference service -- FastAPI, reading only the refined layer.

    uvicorn api.app:app --port 8000        (de dentro de refatoramento/backend)

WHY THIS PROCESS EXISTS. LightGBM does not run in Node. The alternatives --
exporting to ONNX, reimplementing the trees in JS -- trade an infrastructure
problem for a numerical-fidelity one, and fidelity is exactly what cannot slip.

WHAT THIS MODULE DOES NOT DO, each one a recorded decision:

  - **does not persist the request.** An approximate address plus a price band
    identifies a property, and a property has an owner. If usage accounting is
    needed, keep aggregate counts, never the payload;
  - **does not recompute a single feature.** `formulario.linha_do_usuario` is
    the same function training uses. Recomputing `area_por_quarto` with a
    different zero-division rule yields a plausible, wrong prediction and no
    exception;
  - **does not read the processed layer.** Only `03_refined` and `modelos/`.
    A path into `02_processed` here means the migration did not finish.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from api import artefato as A
from api import mercado as mercado_mod
from api import resolucao
from api.contrato import Estimativa
from comum.ml import formulario

RAIZ = Path(__file__).resolve().parents[2]
ENGENHARIA = Path(os.environ.get("ALUGUELCERTO_ENGENHARIA", RAIZ / "engenharia"))
MODELOS = Path(os.environ.get("ALUGUELCERTO_MODELOS", ENGENHARIA / "modelos"))
CIDADES = Path(os.environ.get("ALUGUELCERTO_CIDADES",
                              ENGENHARIA / "config" / "cidades.yaml"))
REFINED = Path(os.environ.get("ALUGUELCERTO_REFINED",
                              ENGENHARIA / "dados" / "03_refined"))

app = FastAPI(title="Aluguel Certo — estimativa", version="2",
              description=__doc__)


def _origens() -> list[str]:
    bruto = os.environ.get("ALUGUELCERTO_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in bruto.split(",") if o.strip()]


ORIGENS = _origens()
# `allow_credentials` com origem `*` e recusado pelo navegador e e o erro de
# configuracao de CORS mais comum. Aqui ele e desligado por construcao quando
# alguem abre a origem, em vez de virar um bug dificil de enxergar.
CURINGA = "*" in ORIGENS

app.add_middleware(
    CORSMiddleware, allow_origins=ORIGENS, allow_credentials=not CURINGA,
    allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"],
    max_age=600)


# --------------------------------------------------------------------------
# disponibilidade
# --------------------------------------------------------------------------

@app.get("/cidades")
def cidades() -> dict:
    """
    Every pair, available or not, with the reason.

    THE SCREEN NEEDS THE UNAVAILABLE ONES TOO. Hiding a city makes the user
    conclude we do not cover the place; showing it disabled says we do not
    cover it YET, which is the truth. And the reason text lives here rather
    than on the screen: written in both places it diverges the first time one
    of them changes.
    """
    cat = resolucao.catalogo(str(CIDADES))
    por_cidade: dict[str, dict] = {}
    for p in cat:
        entrada = por_cidade.setdefault(
            p.cidade, {"cidade": p.cidade, "alvos": {}})
        entrada["alvos"][p.alvo] = {
            "disponivel": p.disponivel,
            "motivo": None if p.disponivel else (
                p.motivo or "modelo estatistico ainda nao disponivel"),
            "n_treino_medido": p.n_treino_medido,
        }
    lista = sorted(por_cidade.values(), key=lambda c: c["cidade"])
    return {"n": len(lista),
            "disponiveis": sum(1 for p in cat if p.disponivel),
            "cidades": lista}


# --------------------------------------------------------------------------
# mercado
# --------------------------------------------------------------------------

@app.get("/mercado")
def mercado(cidade: str | None = Query(default=None),
            mes: str | None = Query(default=None)) -> dict:
    """City-level indicators, one query on the market table."""
    return mercado_mod.por_cidade(REFINED, cidade, mes)


@app.get("/bairros/indicadores")
def bairros_indicadores(cidade: str | None = Query(default=None),
                        mes: str | None = Query(default=None),
                        com_coordenada: bool = Query(default=False)) -> dict:
    """
    Neighbourhood indicators, the same table with a filter.

    `com_coordenada=true` returns only what the map can draw. A neighbourhood
    with no coordinate keeps `lat` null and never receives the city centroid:
    an invented point is indistinguishable from a measured one.
    """
    return mercado_mod.por_bairro(REFINED, cidade, mes, com_coordenada)


# --------------------------------------------------------------------------
# modelo
# --------------------------------------------------------------------------

@app.get("/modelo/card")
def modelo_card(cidade: str = Query(...), alvo: str = Query(...)) -> dict:
    """The whole card of the model that would answer for this pair."""
    try:
        pasta, cartao = resolucao.resolve(MODELOS, str(CIDADES), cidade, alvo)
    except resolucao.SemModelo as exc:
        raise HTTPException(status_code=422, detail={
            "cidade": exc.cidade, "alvo": exc.alvo, "motivo": exc.motivo,
        }) from exc
    return {"caminho": str(pasta.relative_to(MODELOS)), "cartao": cartao}


@app.post("/estimativa")
def estimativa(pedido: Estimativa) -> dict:
    """
    One estimate, from the model of the requested (city, target) pair.

    A pair with no model answers **422 with the reason**, never an approximate
    number and never a 500. The old service picked its segment at start-up and
    could only refuse by saying the request did not match the segment it
    happened to be serving, which told the user nothing they could act on.

    THE RESPONSE CARRIES ITS OWN PROVENANCE: which city answered, which month
    the model is from, what scope it was trained at, and how many rows are
    behind it. Without that block the caller cannot tell a well-supported
    estimate from a thin one.
    """
    try:
        pasta, cartao = resolucao.resolve(
            MODELOS, str(CIDADES), pedido.cidade, pedido.transacao)
    except resolucao.SemModelo as exc:
        raise HTTPException(status_code=422, detail={
            "cidade": exc.cidade, "alvo": exc.alvo, "motivo": exc.motivo,
        }) from exc

    try:
        art = A.carrega(pasta)
    except A.ArtefatoInvalido as exc:
        raise HTTPException(503, str(exc)) from exc

    resposta = pedido.para_modelo()
    linha = formulario.linha_do_usuario(resposta)
    p = art.preve(linha)

    preco = round(float(p["estimativa"][0]), 2)
    lo, hi = round(float(p["min"][0]), 2), round(float(p["max"][0]), 2)

    ressalvas = formulario.ressalvas(resposta)
    # A faixa treinada vem do cartao, e serve para a resposta poder dizer que
    # o pedido caiu fora dela. Sem isto um imovel de doze milhoes recebe um
    # numero com a mesma cara de confianca de um de seiscentos mil.
    faixa = (cartao.get("limites") or {}).get("faixa_de_preco") or {}
    if faixa and not (faixa["p01"] <= preco <= faixa["p99"]):
        ressalvas.append(
            f"o valor estimado cai fora da faixa em que o modelo foi treinado "
            f"(R$ {faixa['p01']:,.0f} a R$ {faixa['p99']:,.0f}); "
            f"trate-o como indicacao, nao como estimativa")

    ident, escopo = cartao["identificacao"], cartao["escopo"]
    return {
        "estimativa": preco,
        "intervalo": {"min": lo, "max": hi},
        "campos_ausentes": formulario.campos_ausentes(resposta),
        "ressalvas": ressalvas,
        "modelo": {
            "cidade": ident["cidade"], "alvo": ident["alvo"],
            "ano": ident["ano"], "mes": ident["mes"],
            "versao": ident["versao"],
            "escopo_treino": escopo["escopo_treino"],
            "n_treino": escopo["n_treino"],
            "periodo_dos_dados": escopo.get("periodo_dos_dados"),
        },
    }


# --------------------------------------------------------------------------
# saude
# --------------------------------------------------------------------------

@app.get("/saude")
def saude() -> dict:
    return {"ok": True}


@app.get("/pronto")
def pronto(resposta: Response) -> dict:
    """
    Ready means the refined layer is there AND every available pair has a model.

    Checking only that the process is up would let the first estimate be the
    thing that discovers a missing model, which turns an operational fault into
    a user-facing error.
    """
    problemas = []
    if not (REFINED / "mercado").exists():
        problemas.append("camada refinada ausente: mercado/")

    for p in resolucao.catalogo(str(CIDADES)):
        if not p.disponivel:
            continue
        if resolucao.pasta_do_modelo(MODELOS, p.cidade, p.alvo) is None:
            problemas.append(f"sem modelo para {p.cidade}/{p.alvo}")

    if problemas:
        resposta.status_code = 503
    return {"pronto": not problemas, "problemas": problemas}
