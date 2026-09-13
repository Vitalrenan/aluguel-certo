"""
Aluguel derivado do preço, pelo yield da cidade.

POR QUE ISTO EXISTE. O modelo prevê **preço de venda**. O produto responde
**aluguel**. A ponte entre os dois é o yield da cidade -- quanto de aluguel
mensal um imóvel rende sobre o seu valor -- e ela é uma **premissa de mercado,
não uma previsão**.

Essa distinção é a razão de o resultado carregar `tipo: "ancora_de_mercado"`. Um
front que receba um número sem essa marca vai apresentá-lo como "o modelo
calculou", e não foi o modelo que calculou: foi uma multiplicação por uma taxa
de referência. É a mesma exigência que o `plano-ux-wireframes.md` §10.2 já fazia.

TRÊS REGRAS QUE O MÓDULO IMPÕE:

1. **Cidade sem yield não recebe estimativa de aluguel.** Devolve `None` e o
   motivo. Não existe taxa padrão, não existe média nacional de reserva. Um
   número inventado aqui sairia com a mesma cara de um medido, e o usuário não
   teria como distinguir.

2. **A incerteza do yield entra na faixa.** A dispersão medida em Santos vai de
   0,438% a 0,882% -- o extremo alto é **duas vezes** o baixo, e essa variação é
   maior que a do próprio modelo de preço. Compor só o intervalo do preço com o
   yield mediano produziria uma faixa estreita e falsa. A faixa de aluguel usa
   o piso do preço com o yield p10 e o teto do preço com o p90.

3. **A tabela é dado, não código.** A fonte primária é o microserviço
   `servicos/fipezap`, que baixa a série histórica publicada e grava
   `data/02_processed/referencia/fipezap/`. `yield_por_cidade.json` continua
   como complemento manual, e só entra onde a FipeZAP não publica a cidade.
   Cada linha declara fonte, mês de referência e ressalvas.

DE ONDE VEM A FAIXA, e é a parte que exige atenção. A FipeZAP publica **um**
número por cidade — o yield agregado do mês. Ela não publica a dispersão entre
imóveis, que é a que importa na tela: dois apartamentos da mesma cidade rendem
taxas diferentes. Medido em 2026-09-09, a série da FipeZAP para Santos oscilou
entre 0,694% e 0,730% em 24 meses (±2,6%), enquanto a dispersão ENTRE IMÓVEIS na
nossa base vai de 0,438% a 0,882% (±41%). Usar a variação temporal como faixa
daria um intervalo dezesseis vezes estreito demais. Por isso a faixa aplica ao
centro da FipeZAP o espalhamento relativo que medimos (MEDICOES §5) — e isso é
uma **suposição declarada**, que viaja em `ressalvas` até a resposta da API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REFERENCIA = Path(__file__).resolve().parents[2] / "data/02_processed/referencia"
FIPEZAP = REFERENCIA / "fipezap"
BAIRROS = REFERENCIA / "bairros"
TABELA = REFERENCIA / "yield_por_cidade.json"   # fallback manual


class YieldIndisponivel(Exception):
    """A tabela não existe ou está malformada. Levanta; não devolve padrão."""


@dataclass(frozen=True)
class Yield:
    cidade: str
    mediana: float
    p10: float
    p90: float
    fonte: str
    referencia: str
    medido_em: str
    n: int | None = None
    metodo: str = ""
    ressalvas: tuple[str, ...] = ()

    def para_json(self) -> dict:
        return {
            "cidade": self.cidade,
            "mensal": self.mediana,
            "mensal_p10": self.p10,
            "mensal_p90": self.p90,
            "fonte": self.fonte,
            "referencia": self.referencia,
            "medido_em": self.medido_em,
            "n": self.n,
            "ressalvas": list(self.ressalvas),
        }


def _do_fipezap(base: Path) -> dict[str, Yield]:
    """
    A tabela do microserviço `servicos/fipezap`. Fonte primária.

    O ponteiro `mais_recente.json` existe para o consumidor não ter de descobrir
    qual arquivo é o novo — descobrir por ordenação de nome funciona até o mês
    em que não funciona.
    """
    ponteiro = base / "mais_recente.json"
    if not ponteiro.exists():
        return {}
    alvo = base / json.loads(ponteiro.read_text(encoding="utf-8"))["arquivo"]
    if not alvo.exists():
        raise YieldIndisponivel(
            f"{ponteiro} aponta para {alvo.name}, que não existe. Rode "
            f"`python servicos/fipezap/coletar.py --write`.")
    t = json.loads(alvo.read_text(encoding="utf-8"))
    saida: dict[str, Yield] = {}
    for cidade, d in t["cidades"].items():
        disp = d.get("dispersao") or {}
        if not disp.get("p10") or not disp.get("p90"):
            continue
        saida[cidade] = Yield(
            cidade=cidade, mediana=float(d["yield_mensal"]),
            p10=float(disp["p10"]), p90=float(disp["p90"]),
            fonte="fipezap",
            referencia=f"Índice FipeZAP, série histórica, {d['referencia']}",
            medido_em=d["referencia"], n=None,
            metodo=("razão entre aluguel anualizado e valor de venda no mesmo "
                    "local, publicada mensalizada pela FipeZAP"),
            ressalvas=(
                disp.get("origem", ""),
                f"índice agregado da cidade — não distingue bairro nem tipo; "
                f"em Santos o yield varia por bairro na nossa própria base",
                f"mês de referência {d['referencia']}; reexecutar o coletor "
                f"mensalmente",
            ))
    return saida


@lru_cache(maxsize=1)
def _carrega(caminho: str | None = None) -> dict[str, Yield]:
    """
    FipeZAP primeiro; a tabela manual completa o que faltar.

    A ordem importa e é deliberada: a FipeZAP é fonte pública, mensal e de 37
    cidades; a tabela manual tem uma entrada medida por nós. Onde as duas têm a
    mesma cidade, **a FipeZAP vence** — é a fonte que o produto declara usar.
    Onde só a nossa existe, ela entra, com `fonte: "propria"` na resposta para
    que a tela possa distinguir.
    """
    if caminho is None:
        saida = dict(_do_fipezap(FIPEZAP))
        if TABELA.exists():
            for cidade, y in _do_arquivo(TABELA).items():
                saida.setdefault(cidade, y)
        if not saida:
            raise YieldIndisponivel(
                f"nenhuma fonte de yield. Rode "
                f"`python servicos/fipezap/coletar.py --write`.")
        return saida
    return _do_arquivo(Path(caminho))


def _do_arquivo(p: Path) -> dict[str, Yield]:
    if not p.exists():
        raise YieldIndisponivel(f"tabela de yield ausente: {p}")
    try:
        bruto = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise YieldIndisponivel(f"{p} não é JSON válido: {e}") from e
    saida: dict[str, Yield] = {}
    for cidade, d in (bruto.get("cidades") or {}).items():
        faltam = [k for k in ("mediana", "p10", "p90", "fonte", "referencia",
                              "medido_em") if k not in d]
        if faltam:
            # Entrada sem procedência é pior que entrada ausente: ela seria
            # usada, e ninguém saberia de onde veio o número na tela.
            raise YieldIndisponivel(
                f"yield de {cidade!r} sem {faltam} -- toda entrada declara "
                f"fonte e data, ou não entra")
        if not (0 < d["p10"] <= d["mediana"] <= d["p90"] < 0.05):
            raise YieldIndisponivel(
                f"yield de {cidade!r} fora de ordem ou implausível: "
                f"p10={d['p10']} mediana={d['mediana']} p90={d['p90']}. "
                f"Faixa aceita: 0 < p10 <= mediana <= p90 < 5% ao mês.")
        saida[cidade] = Yield(
            cidade=cidade, mediana=float(d["mediana"]), p10=float(d["p10"]),
            p90=float(d["p90"]), fonte=str(d["fonte"]),
            referencia=str(d["referencia"]), medido_em=str(d["medido_em"]),
            n=d.get("n"), metodo=d.get("metodo", ""),
            ressalvas=tuple(d.get("ressalvas", ())))
    return saida


def cidades(caminho: str | None = None) -> list[str]:
    """Quais cidades têm yield. O front usa para saber onde pode oferecer aluguel."""
    return sorted(_carrega(caminho))


def bairros(base: Path | None = None) -> dict:
    """
    Indicadores por bairro, do `construir_indicadores_bairro.py`.

    Serve a home: contagem, preço mediano, R$/m², faixa de tercil e centroide.
    Bairro sem coordenada vem com `lat`/`lon` nulos — o mapa desenha o que
    existe e o resumo diz quantos ficaram de fora.
    """
    base = base or BAIRROS
    ponteiro = base / "mais_recente.json"
    if not ponteiro.exists():
        raise YieldIndisponivel(
            f"sem tabela de bairros em {base}. Rode "
            f"`python construir_indicadores_bairro.py --write`.")
    p = json.loads(ponteiro.read_text(encoding="utf-8"))
    alvo = base / p["arquivo"]
    if not alvo.exists():
        raise YieldIndisponivel(f"{ponteiro} aponta para {alvo.name}, ausente")
    return json.loads(alvo.read_text(encoding="utf-8"))


def indicadores(base: Path | None = None) -> dict:
    """
    A tabela de mercado inteira, como o microserviço a gravou.

    O backend serve isto ao front sem reescrever nada: preço médio de venda e
    de locação por m², a rentabilidade, e a procedência. Reformatar aqui criaria
    um segundo esquema para manter em dia, e é o tipo de cópia que diverge da
    original sem levantar exceção.
    """
    base = base or FIPEZAP
    ponteiro = base / "mais_recente.json"
    if not ponteiro.exists():
        raise YieldIndisponivel(
            f"sem tabela de mercado em {base}. Rode "
            f"`python servicos/fipezap/coletar.py --write`.")
    p = json.loads(ponteiro.read_text(encoding="utf-8"))
    alvo = base / p["arquivo"]
    if not alvo.exists():
        raise YieldIndisponivel(f"{ponteiro} aponta para {alvo.name}, ausente")
    t = json.loads(alvo.read_text(encoding="utf-8"))
    return {"fonte": t["fonte"], "referencia": p["referencia"],
            "coletado_em": t["coletado_em"],
            "cidades_sem_rentabilidade": t.get("cidades_sem_rentabilidade", []),
            "cidades": t["cidades"]}


def para(cidade: str, caminho: str | None = None) -> Yield | None:
    """O yield da cidade, ou `None` — nunca um padrão inventado."""
    return _carrega(caminho).get(str(cidade).strip())


def aluguel(preco: float, preco_min: float, preco_max: float, cidade: str,
            caminho: str | None = None) -> dict | None:
    """
    Aluguel mensal a partir do preço estimado e da faixa do preço.

    A faixa de saída compõe as DUAS incertezas -- a do modelo de preço e a do
    yield. Em Santos o yield vai de 0,438% a 0,882%, uma variação de 2x que é
    maior que a do preço; ignorá-la entregaria uma faixa estreita e errada.

    `None` quando a cidade não tem yield. Quem chama decide o que dizer ao
    usuário; o que não se faz é preencher com uma taxa qualquer.
    """
    y = para(cidade, caminho)
    if y is None:
        return None
    envelope = {"min": round(preco_min * y.p10, 2),
                "max": round(preco_max * y.p90, 2)}
    ponto = round(preco * y.mediana, 2)
    return {
        "estimativa": ponto,
        # NÃO se chama `intervalo` de propósito. O intervalo do PREÇO é uma
        # banda quantílica com cobertura medida no holdout (72,9%). Isto aqui é
        # outra coisa: o pior caso de duas incertezas multiplicadas, e a
        # cobertura dele não foi medida -- nem pode ser, enquanto não houver
        # aluguel real pareado com preço real em volume. Dar o mesmo nome aos
        # dois faria a tela tratá-los como equivalentes.
        "envelope": envelope,
        "envelope_cobertura_medida": None,
        "largura_do_envelope": round(envelope["max"] / envelope["min"], 2),
        "tipo": "ancora_de_mercado",
        "yield": y.para_json(),
        "nota": (
            "Derivado do preço estimado multiplicado pelo yield da cidade. NÃO "
            "é previsão de modelo de aluguel: é taxa de referência de mercado "
            "aplicada a um preço previsto. O `envelope` compõe a incerteza do "
            "preço com a do próprio yield, que nesta cidade varia "
            f"{y.p90 / y.p10:.1f}x entre p10 e p90 -- por isso ele é largo, e "
            "por isso não tem cobertura medida como a do preço."),
    }


def motivo_da_ausencia(cidade: str, caminho: str | None = None) -> str:
    """Texto para a resposta da API quando não há yield. Diz o que falta."""
    disponiveis = cidades(caminho)
    return (f"Não há yield de aluguel para {cidade}. O aluguel só é estimado "
            f"onde existe taxa de referência com fonte declarada; hoje: "
            f"{', '.join(disponiveis) or 'nenhuma cidade'}. "
            f"Estimar fora disso seria inventar a taxa.")
