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
from api import telas
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


@app.get("/")
def indice() -> dict:
    """
    O que existe neste serviço.

    Sem esta rota a raiz devolve `{"detail":"Not Found"}` -- tecnicamente
    correto e inútil para quem abriu a URL para ver o que há. Quem chega aqui
    está descobrindo o serviço, não consumindo dado, e merece um mapa.
    """
    return {
        "servico": "Aluguel Certo — estimativa de preço de imóvel",
        "documentacao": "/docs",
        "rotas": {
            "GET /cidades": "pares cidade × transação, com disponibilidade e motivo",
            "POST /estimativa": "a estimativa; par sem modelo responde 422 com o motivo",
            "GET /mercado": "indicadores por cidade",
            "GET /bairros/indicadores": "indicadores por bairro",
            "GET /modelo/card": "o model card do par (cidade, alvo)",
            "GET /saude": "o processo está de pé",
            "GET /pronto": "a camada refinada existe e um modelo carrega",
        },
    }


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
    """
    Indicadores por cidade, uma consulta na tabela de mercado.

    `cidades` é DICIONÁRIO indexado pelo nome, e não lista: é o formato que a
    tela consome. Devolver lista fazia `mercado.cidades[cidade]` valer
    `undefined`, e a tela quebrava lendo um campo dele.
    """
    tabela = mercado_mod.carrega(REFINED, mes)
    resposta = telas.mercado(tabela)
    if cidade:
        d = resposta["cidades"].get(cidade)
        if d is None:
            raise HTTPException(
                404, f"sem indicadores de mercado para {cidade!r}. "
                     f"Disponíveis: {sorted(resposta['cidades'])}")
        return {"fonte": resposta["fonte"], "referencia": resposta["referencia"],
                "cidade": cidade, "indicadores": d}
    return resposta


@app.get("/bairros/indicadores")
def bairros_indicadores(cidade: str | None = Query(default=None),
                        mes: str | None = Query(default=None),
                        com_coordenada: bool = Query(default=False)) -> dict:
    """
    Indicadores por bairro, a mesma tabela com filtro.

    `com_coordenada=true` devolve só o que o mapa desenha. Bairro sem
    coordenada fica com `lat` nula e nunca recebe o centroide da cidade: ponto
    inventado é indistinguível de medido.
    """
    tabela = mercado_mod.carrega(REFINED, mes)
    return telas.bairros(tabela, cidade, com_coordenada)


@app.get("/modelo")
def modelo(completo: bool = Query(default=False),
           cidade: str | None = Query(default=None),
           alvo: str | None = Query(default=None)) -> dict:
    """
    Proveniência do número: de quando é a base, quantos imóveis, qual erro.

    É o que separa "a IA disse" de uma estimativa auditável, e a tela a mostra
    ao lado do resultado.

    Sem `cidade` e `alvo` responde pelo PRIMEIRO par disponível. O serviço
    antigo servia um segmento só, e a tela ainda chama esta rota sem
    argumento; recusar aqui quebraria a home por falta de um parâmetro que ela
    nunca teve motivo para mandar.
    """
    pares = [p for p in resolucao.catalogo(str(CIDADES)) if p.disponivel]
    if not pares:
        raise HTTPException(503, "nenhum par disponível")
    alvo_par = pares[0] if not (cidade and alvo) else None

    try:
        _, cartao = resolucao.resolve(
            MODELOS, str(CIDADES),
            cidade or alvo_par.cidade, alvo or alvo_par.alvo)
    except resolucao.SemModelo as exc:
        raise HTTPException(status_code=422, detail={
            "cidade": exc.cidade, "alvo": exc.alvo, "motivo": exc.motivo,
        }) from exc

    return cartao if completo else telas.metadados(cartao)


@app.get("/bairros")
def bairros(cidade: str | None = Query(default=None)) -> dict:
    """
    Os bairros que o modelo conhece.

    Impede o pior erro de formulário livre: o usuário digita um bairro que não
    casa com nenhuma categoria, vira nulo, e a segunda variável mais forte da
    base é perdida em silêncio.
    """
    tabela = mercado_mod.carrega(REFINED)
    d = tabela
    if cidade:
        d = d[d.cidade.map(resolucao.slug) == resolucao.slug(cidade)]
    lista = sorted(d.bairro.dropna().unique().tolist())
    return {"cidade": cidade, "filtrado": bool(cidade),
            "n": len(lista), "bairros": lista}


@app.get("/opcoes")
def opcoes() -> dict:
    """
    O formulário inteiro e o vocabulário fechado de cada categórica.

    Os níveis vêm do ARTEFATO, não do código: é o treino que define o que o
    modelo conhece, e um artefato mais velho que o código continua servindo o
    seu próprio vocabulário em vez de um inventado.
    """
    pares = [p for p in resolucao.catalogo(str(CIDADES)) if p.disponivel]
    if not pares:
        raise HTTPException(503, "nenhum par disponível")
    pasta, _ = resolucao.resolve(MODELOS, str(CIDADES),
                                 pares[0].cidade, pares[0].alvo)
    art = A.carrega(pasta)

    def do_artefato(coluna, traducao=None):
        conhecidos = set(art.niveis(coluna))
        if traducao is None:
            return [{"valor": v, "rotulo": formulario.rotulo(v)}
                    for v in sorted(conhecidos)]
        return [{"valor": k, "rotulo": formulario.rotulo(k)}
                for k, dest in traducao.items()
                if dest in conhecidos or dest in formulario.NIVEIS_INTERNOS]

    # As cidades saem do catálogo, não do artefato: o artefato de Santos só
    # conhece Santos, e a tela precisa oferecer todas as que o serviço atende.
    vocabulario = {
        "transacao": [{"valor": a, "rotulo": formulario.rotulo(a)}
                      for a in sorted({p.alvo for p in pares})],
        "cidade": [{"valor": c, "rotulo": c}
                   for c in sorted({p.cidade for p in pares})],
        "tipo": do_artefato("property_type"),
        "vaga_tipo": do_artefato("rx_vaga_tipo", formulario.VAGA_USUARIO),
        "vista": do_artefato("rx_vista", formulario.VISTA_USUARIO),
    }
    perguntas = [{
        "api": p.api, "rotulo": p.rotulo, "tipo": p.tipo,
        "obrigatoria": p.obrigatoria,
        "faixa": list(p.faixa) if p.faixa else None,
        "opcoes": vocabulario.get(p.api),
        "delta_rmse": p.delta_rmse, "nota": p.nota,
    } for p in formulario.PERGUNTAS]

    return {"perguntas": perguntas, "vocabulario": vocabulario,
            "bairro": "use GET /bairros — a lista é longa demais para esta rota"}


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
            f"trate-o como indicação, não como estimativa")

    # O modelo prevê PREÇO. O aluguel sai do rendimento publicado da cidade
    # aplicado a esse preço -- premissa de mercado, não previsão, e por isso o
    # bloco carrega `tipo: "ancora_de_mercado"`.
    aluguel, motivo = None, None
    if pedido.transacao == "venda":
        try:
            tabela = mercado_mod.carrega(REFINED)
            aluguel, motivo = telas.aluguel(preco, lo, hi, pedido.cidade, tabela)
        except mercado_mod.MercadoAusente as exc:
            motivo = str(exc)
    if motivo:
        ressalvas.append(motivo)

    return {
        "estimativa": preco,
        "intervalo": {"min": lo, "max": hi},
        "aluguel": aluguel,
        "modelo": telas.resumo_do_modelo(cartao),
        "campos_ausentes": formulario.campos_ausentes(resposta),
        "ressalvas": ressalvas,
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

    primeiro = True
    for p in resolucao.catalogo(str(CIDADES)):
        if not p.disponivel:
            continue
        pasta = resolucao.pasta_do_modelo(MODELOS, p.cidade, p.alvo)
        if pasta is None:
            problemas.append(f"sem modelo para {p.cidade}/{p.alvo}")
            continue

        # CARREGA UM MODELO DE VERDADE, uma vez. Conferir que o diretório
        # existe não prova que ele abre: `libgomp1` faltava na imagem e o
        # LightGBM só é importado quando o primeiro booster é carregado, então
        # `/pronto` respondia 200 enquanto toda estimativa devolvia 500. Uma
        # prontidão que não exercita o caminho que serve não está verificando
        # prontidão nenhuma.
        if primeiro:
            primeiro = False
            try:
                A.carrega(pasta)
            except Exception as exc:
                problemas.append(
                    f"modelo de {p.cidade}/{p.alvo} nao carrega: "
                    f"{type(exc).__name__}: {exc}")

    if problemas:
        resposta.status_code = 503
    return {"pronto": not problemas, "problemas": problemas}
