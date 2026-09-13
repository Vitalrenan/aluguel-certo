"""
Montagem do intervalo de previsão — uma função, dois consumidores.

Existe para que a cobertura medida no holdout descreva **o intervalo que o
usuário recebe**, e não um parecido com ele. Se o `treina_final.py` medisse a
cobertura dos quantis crus e o serviço entregasse os quantis ordenados e presos
ao ponto central, o número em `metadados.json` seria de outra faixa — e ninguém
notaria, porque os dois são plausíveis e nenhum levanta exceção.

Por isso a regra do Arquitetura §4 vale aqui também: **importar, não copiar.**

DUAS CORREÇÕES, e as duas são necessárias:

  1. **ordenar.** `q10` e `q90` são boosters independentes; nada os obriga a
     sair em ordem. Cruzamento de quantis é conhecido na literatura e acontece
     de fato nesta base -- o artefato conta quantos houve no holdout;
  2. **prender o ponto central dentro da faixa.** O ponto vem de um terceiro
     booster, de perda quadrática, e pode cair fora dos quantis. Um limite
     inferior ACIMA da estimativa é absurdo na tela, e absurdo na tela é o que
     faz o usuário parar de acreditar também no número que está certo.

O que NÃO se faz aqui é alargar a faixa para forçar cobertura. A cobertura é
medida e reportada como saiu; se ela não bater com os 80% nominais, o conserto
é no modelo ou na escolha dos quantis, não na régua.
"""
from __future__ import annotations

import numpy as np


def monta(central: np.ndarray, q_baixo: np.ndarray, q_alto: np.ndarray
          ) -> dict[str, np.ndarray]:
    """
    Recebe os três em ESCALA DE PREÇO (já exponenciados), devolve o que sai.

    `cruzou` marca as linhas em que os dois quantis vieram invertidos. Ela viaja
    junto para que quem mede possa contar, em vez de a correção acontecer calada.
    """
    central = np.asarray(central, dtype=float)
    q_baixo = np.asarray(q_baixo, dtype=float)
    q_alto = np.asarray(q_alto, dtype=float)
    cruzou = q_alto < q_baixo
    lo = np.minimum(q_baixo, q_alto)
    hi = np.maximum(q_baixo, q_alto)
    return {"estimativa": central,
            "min": np.minimum(lo, central),
            "max": np.maximum(hi, central),
            "cruzou": cruzou}


def cobertura(real: np.ndarray, faixa: dict[str, np.ndarray]) -> dict:
    """Quanto da faixa entregue de fato contém o preço real."""
    real = np.asarray(real, dtype=float)
    lo, hi, ponto = faixa["min"], faixa["max"], faixa["estimativa"]
    dentro = (real >= lo) & (real <= hi)
    return {
        "cobertura_intervalo": float(dentro.mean()),
        "largura_mediana_brl": float(np.median(hi - lo)),
        "largura_mediana_pct": float(np.median((hi - lo) / ponto)),
        "cruzamentos": int(np.asarray(faixa["cruzou"]).sum()),
    }
