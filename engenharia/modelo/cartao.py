"""
The model card -- six blocks, and the last one did not exist before.

`metadados.json` carried 22 fields of honest provenance: origin base, training
date, holdout metrics, decile calibration. What it never carried was what the
model should NOT be asked.

Without the `limites` block, somebody asking for an estimate on a twelve-million
property gets a number wearing the same confidence as one on six hundred
thousand. With it, the API has somewhere to read that the request fell outside
the trained range, and can say so instead of answering.

THE CARD IS NOT OPTIONAL AND NOT PARTIAL. `valida` refuses a card missing
`escopo_treino`, because a card that does not say whether the model is the
city's own or a pooled one is worse than no card: it looks complete.
"""
from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BLOCOS = ("identificacao", "escopo", "dados", "desempenho", "limites", "ambiente")

ESCOPOS = ("cidade", "agrupado")


class CartaoInvalido(Exception):
    """A card that cannot be trusted is not written."""


def _commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
    except Exception:
        return None


def limites(treino: pd.DataFrame, alvo_coluna: str = "price") -> dict:
    """
    What the model was NOT trained on, said in numbers a caller can check.

    The ranges are the observed 1st and 99th percentiles rather than min and
    max: a single mislabelled listing at R$ 1 would otherwise widen the stated
    range to cover everything and the limit would stop limiting.
    """
    def faixa(col):
        if col not in treino or treino[col].notna().sum() < 10:
            return None
        s = treino[col].dropna()
        return {"p01": float(s.quantile(0.01)), "p99": float(s.quantile(0.99)),
                "min": float(s.min()), "max": float(s.max())}

    bairros = {}
    if "neighborhood" in treino:
        contagem = treino.neighborhood.value_counts()
        bairros = {str(b): int(n) for b, n in contagem[contagem < 30].items()}

    return {
        "uso_pretendido": (
            "Estimativa de preco de anuncio para imovel residencial na cidade "
            "e transacao declaradas em `identificacao`, a partir dos atributos "
            "do formulario."),
        "fora_de_escopo": [
            "imovel comercial, rural ou terreno sem edificacao",
            "cidade diferente da declarada -- nao ha modelo agrupado de reserva",
            "avaliacao para fim legal, garantia ou credito",
            "preco de transacao fechada: o alvo e preco de ANUNCIO",
        ],
        "faixa_de_preco": faixa(alvo_coluna),
        "faixa_de_area": faixa("area_m2"),
        "bairros_com_n_insuficiente": bairros,
        "n_bairros_com_n_insuficiente": len(bairros),
    }


def monta(*, cidade: str, alvo: str, ano: str, mes: str, versao: str,
          escopo_treino: str, treino: pd.DataFrame, holdout: pd.DataFrame,
          abt_caminho: str, abt_sha256: str | None,
          desempenho: dict, cobertura_por_campo: dict) -> dict:
    if escopo_treino not in ESCOPOS:
        raise CartaoInvalido(
            f"escopo_treino {escopo_treino!r} nao e um de {ESCOPOS}")

    plataformas = (sorted(map(str, treino.source_platform.dropna().unique()))
                   if "source_platform" in treino else [])
    periodo = (sorted(map(str, treino.mes_referencia.dropna().unique()))
               if "mes_referencia" in treino else [])

    return {
        "identificacao": {
            "cidade": cidade, "alvo": alvo, "ano": ano, "mes": mes,
            "versao": versao, "commit": _commit(),
        },
        "escopo": {
            "escopo_treino": escopo_treino,
            "n_treino": int(len(treino)),
            "n_treino_cidade": int(
                (treino.city == cidade).sum()) if "city" in treino else int(len(treino)),
            "n_holdout": int(len(holdout)),
            "periodo_dos_dados": periodo,
        },
        "dados": {
            "abt": abt_caminho,
            "abt_sha256": abt_sha256,
            "plataformas": plataformas,
            "cobertura_por_campo": cobertura_por_campo,
        },
        "desempenho": desempenho,
        "limites": limites(treino),
        "ambiente": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "lightgbm": _versao("lightgbm"),
            "gerado_em": datetime.now(timezone.utc).isoformat(),
        },
    }


def _versao(nome: str) -> str | None:
    try:
        import importlib.metadata as md
        return md.version(nome)
    except Exception:
        return None


def valida(cartao: dict) -> None:
    """
    Refuses a card that cannot be trusted. Raises; never writes a partial one.

    `escopo_treino` is checked by name because it is the one field a reader
    cannot infer from the rest: a pooled model and a city model produce cards
    that look identical everywhere else.
    """
    faltando = [b for b in BLOCOS if b not in cartao]
    if faltando:
        raise CartaoInvalido(f"blocos ausentes: {faltando}")

    escopo = cartao["escopo"].get("escopo_treino")
    if escopo not in ESCOPOS:
        raise CartaoInvalido(
            f"escopo_treino ausente ou invalido ({escopo!r}). "
            "Um cartao que nao diz se o modelo e da cidade ou agrupado e pior "
            "que nenhum: parece completo.")

    if not cartao["escopo"].get("n_treino"):
        raise CartaoInvalido("n_treino ausente ou zero")
    if not cartao["limites"].get("uso_pretendido"):
        raise CartaoInvalido("limites.uso_pretendido ausente")


def grava(cartao: dict, pasta: str | Path) -> Path:
    """Validate, then write. Never the other way round."""
    valida(cartao)
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / "model_card.json"
    try:
        alvo.write_text(json.dumps(cartao, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception as exc:
        raise CartaoInvalido(f"falha ao gravar {alvo}: {exc}") from exc
    return alvo
