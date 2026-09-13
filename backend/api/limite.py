"""
Limite de taxa por cliente — janela deslizante, em processo.

POR QUE EXISTE AGORA. Enquanto havia um BFF planejado, o limite moraria nele.
Sem BFF, o navegador fala direto com este processo, e `POST /estimativa` roda
três boosters LightGBM por chamada — é CPU, não leitura de cache. Sem limite,
um laço de `fetch` num cliente derruba o serviço para todo mundo.

PRIVACIDADE, e ela restringe o desenho. A Arquitetura §9 diz que o serviço não
persiste requisição. Um limitador precisa distinguir clientes, e o identificador
natural é o IP, que é dado pessoal. Duas medidas:

  - **o IP nunca é guardado em claro.** A chave é `sha256(ip + sal)[:16]`, com
    sal sorteado na subida do processo. Reiniciar o serviço torna as chaves
    antigas irreconstruíveis;
  - **nada vai a disco e nada é registrado em log.** Os contadores vivem em
    memória e somem com o processo.

LIMITAÇÃO DECLARADA: contador em processo. Com duas réplicas, o limite efetivo
dobra. Para valer entre réplicas seria preciso um armazenamento compartilhado, e
isso não existe aqui — está escrito para ninguém supor proteção que não tem.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from collections import deque


class Limitador:
    """
    Janela deslizante: N eventos por `janela` segundos, por chave.

    Deslizante e não fixa de propósito. Numa janela fixa de 60s, um cliente faz
    N chamadas em 0:59 e mais N em 1:01 — 2N em dois segundos, dentro do limite
    nominal. A deslizante não tem essa borda.
    """

    def __init__(self, limite: int, janela: float = 60.0):
        if limite < 1:
            raise ValueError("limite tem de ser >= 1")
        self.limite = limite
        self.janela = janela
        self._eventos: dict[str, deque[float]] = {}
        self._trava = threading.Lock()
        self._sal = secrets.token_bytes(16)

    def chave(self, identificador: str) -> str:
        """IP -> chave opaca. O IP em claro não sobrevive a esta função."""
        return hashlib.sha256(self._sal + identificador.encode()).hexdigest()[:16]

    def permite(self, identificador: str, agora: float | None = None
                ) -> tuple[bool, int, float]:
        """
        `(permitido, restantes, segundos_ate_liberar)`.

        Só registra o evento quando permite. Contar a chamada recusada faria o
        cliente que insiste nunca sair do bloqueio — punição crescente por
        tentar de novo, que não é o que um limite de taxa deve fazer.
        """
        agora = time.monotonic() if agora is None else agora
        k = self.chave(identificador)
        with self._trava:
            fila = self._eventos.setdefault(k, deque())
            corte = agora - self.janela
            while fila and fila[0] <= corte:
                fila.popleft()
            if len(fila) >= self.limite:
                return False, 0, max(0.0, fila[0] + self.janela - agora)
            fila.append(agora)
            if len(self._eventos) > 10_000:
                self._limpa(agora)
            return True, self.limite - len(fila), 0.0

    def _limpa(self, agora: float) -> None:
        """Chamado sob trava. Memória não cresce sem teto."""
        corte = agora - self.janela
        for k in [k for k, f in self._eventos.items() if not f or f[-1] <= corte]:
            self._eventos.pop(k, None)

    def esquece(self, identificador: str) -> None:
        with self._trava:
            self._eventos.pop(self.chave(identificador), None)


def _inteiro(nome: str, padrao: int) -> int:
    try:
        v = int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao
    return v if v >= 1 else padrao


# Dois limites, porque os custos são diferentes em ordem de grandeza. Uma
# estimativa ajusta três boosters; `/opcoes` devolve um dicionário em memória.
LIMITE_ESTIMATIVA = _inteiro("ALUGUELCERTO_LIMITE_ESTIMATIVA", 30)
LIMITE_LEITURA = _inteiro("ALUGUELCERTO_LIMITE_LEITURA", 240)

# `/saude` e `/pronto` ficam de fora: são sondas de orquestrador, chamadas com
# frequência alta e por desenho. Limitá-las derrubaria o serviço por parecer
# indisponível.
ISENTAS = frozenset({"/saude", "/pronto"})
