"""
Carga do artefato de modelo — Arquitetura §3.

Um artefato é um diretório `modelos/{segmento}/{versao}/` com seis arquivos, e
os seis são necessários. O erro que este módulo existe para impedir é o de
carregar o booster sozinho: ele não prevê. Precisa das colunas **na ordem**
(o LightGBM casa feature por posição, não por nome -- ordem trocada prevê em
silêncio e errado) e das categorias vistas no treino (bairro que o treino não
viu tem de virar nulo, não uma categoria inventada).

Toda falha de carga levanta na subida do processo. Um serviço que sobe com
metade do artefato responde `200` com número errado, e ninguém fica sabendo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# `pipeline_path` morreu com a refatoracao: ele existia so para empurrar o
# pipeline no sys.path. Agora `engenharia` e pacote instalavel.

from comum.ml import avaliacao, formulario, intervalo  # noqa: E402

ARQUIVOS = ("modelo.txt", "modelo_q10.txt", "modelo_q90.txt",
            "colunas.json", "categorias.json", "metadados.json")


class ArtefatoInvalido(RuntimeError):
    """O diretório não é um artefato utilizável. Nunca engolida."""


@dataclass
class Artefato:
    caminho: Path
    colunas: list[str]
    categorias: dict[str, list[str]]
    metadados: dict
    central: object
    q10: object
    q90: object

    # -- identidade --------------------------------------------------------
    @property
    def versao(self) -> str:
        return self.metadados.get("versao") or self.caminho.name

    @property
    def segmento(self) -> str:
        return self.metadados.get("segmento", "")

    def bairros(self, cidade: str | None = None) -> list[str]:
        """
        Níveis de `neighborhood` que o modelo conhece.

        Existe para o formulário virar `select`. Sem isso o usuário digita
        "Gonzaga " com espaço, não casa com nenhuma categoria, vira nulo, e a
        segunda variável mais forte da base some sem aviso.

        O filtro por cidade sai de `bairros_por_cidade` nos metadados quando o
        treino o gravou; sem ele, devolve tudo -- e diz que devolveu tudo, em
        vez de fingir que filtrou.
        """
        mapa = self.metadados.get("bairros_por_cidade") or {}
        if cidade and mapa:
            return sorted(mapa.get(cidade, []))
        return sorted(self.categorias.get("neighborhood", []))

    def niveis(self, coluna: str) -> list[str]:
        return sorted(self.categorias.get(coluna, []))

    # -- predição ----------------------------------------------------------
    def prepara(self, X: pd.DataFrame) -> pd.DataFrame:
        faltam = [c for c in self.colunas if c not in X.columns]
        if faltam:
            raise ArtefatoInvalido(f"matriz sem as colunas do artefato: {faltam}")
        Y = X[self.colunas].copy()          # a ORDEM vem do artefato
        for c, niveis in self.categorias.items():
            if c not in Y.columns:
                continue
            Y[c] = Y[c].astype(str).where(Y[c].notna()).astype(
                pd.CategoricalDtype(niveis))
        for c in Y.columns:
            if Y[c].dtype == bool:
                Y[c] = Y[c].astype(np.int8)
        return Y

    def preve(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        """
        Ponto central e intervalo, em reais.

        A montagem da faixa vive em `ml.intervalo` e é a MESMA função que o
        `treina_final.py` usa para medir a cobertura no holdout. Se cada lado
        montasse a sua, o número publicado em `metadados.json` descreveria uma
        faixa parecida com a que o usuário recebe, e não a que ele recebe --
        plausível, e errado.
        """
        Xp = self.prepara(X)
        return intervalo.monta(np.exp(self.central.predict(Xp)),
                               np.exp(self.q10.predict(Xp)),
                               np.exp(self.q90.predict(Xp)))


def carrega(caminho: Path | str) -> Artefato:
    import lightgbm as lgb

    p = Path(caminho)
    if not p.is_dir():
        raise ArtefatoInvalido(f"{p} não é um diretório de artefato")
    faltam = [a for a in ARQUIVOS if not (p / a).exists()]
    if faltam:
        raise ArtefatoInvalido(f"{p} incompleto, faltam: {faltam}")

    colunas = json.loads((p / "colunas.json").read_text(encoding="utf-8"))
    categorias = json.loads((p / "categorias.json").read_text(encoding="utf-8"))
    metadados = json.loads((p / "metadados.json").read_text(encoding="utf-8"))
    if not isinstance(colunas, list) or not colunas:
        raise ArtefatoInvalido("colunas.json vazio ou malformado")

    central = lgb.Booster(model_file=str(p / "modelo.txt"))
    # O booster carrega a própria lista de features. Se ela discordar de
    # `colunas.json`, a matriz seria montada numa ordem e consumida noutra --
    # sem erro, com número errado. É a checagem que separa este carregador de
    # um `lgb.Booster(...)` solto.
    do_modelo = list(central.feature_name())
    if do_modelo != list(colunas):
        raise ArtefatoInvalido(
            "colunas.json não bate com as features do booster.\n"
            f"  artefato: {colunas}\n  booster:  {do_modelo}")

    art = Artefato(
        caminho=p, colunas=list(colunas), categorias=categorias,
        metadados=metadados, central=central,
        q10=lgb.Booster(model_file=str(p / "modelo_q10.txt")),
        q90=lgb.Booster(model_file=str(p / "modelo_q90.txt")))
    for nome, m in (("q10", art.q10), ("q90", art.q90)):
        if list(m.feature_name()) != art.colunas:
            raise ArtefatoInvalido(f"{nome} treinado com outras colunas")
    return art


def mais_recente(raiz: Path | str, segmento: str) -> Path:
    """A versão mais nova de um segmento. As versões ordenam lexicograficamente."""
    d = Path(raiz) / segmento
    if not d.is_dir():
        raise ArtefatoInvalido(
            f"nenhum modelo para o segmento '{segmento}' em {d}. "
            f"Rode `python treina_final.py --write`.")
    versoes = sorted(x for x in d.iterdir()
                     if x.is_dir() and not x.name.endswith(".parcial"))
    if not versoes:
        raise ArtefatoInvalido(f"{d} não tem nenhuma versão gravada")
    return versoes[-1]


def caso_sintetico(art: Artefato) -> pd.DataFrame:
    """
    Uma linha plausível montada do próprio artefato, para o `/pronto`.

    Não é fixture de imóvel real: nada de página capturada é persistido neste
    projeto, e um caso de teste com bairro e área de um anúncio de verdade seria
    exatamente isso. Os valores saem das categorias do artefato e das medianas
    do formulário.
    """
    resposta = {
        "transacao": (art.niveis("transaction_type") or ["venda"])[0],
        "cidade": (art.niveis("city") or ["Santos"])[0],
        "bairro": (art.bairros() or ["Centro"])[0],
        "tipo": "apartamento",
        "area_m2": 78.0, "dormitorios": 2, "banheiros": 2, "suites": 1,
        "vagas": 1, "vaga_tipo": "privativa", "andar": 7,
        "varanda": True, "vista": "livre", "piscina": True,
        "elevador": True, "sauna": False, "academia": False,
        "quintal": False, "ar_condicionado": False,
        "condominio": None, "iptu": None,
    }
    return formulario.linha_do_usuario(resposta)


__all__ = ["Artefato", "ArtefatoInvalido", "carrega", "mais_recente",
           "caso_sintetico", "avaliacao", "formulario"]
