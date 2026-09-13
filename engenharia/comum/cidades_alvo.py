"""
As 15 cidades-alvo da coleta, como objetos.

ESTA É A LISTA FECHADA. Coleta de anúncio acontece nestas quinze e em mais
nenhuma. Um domínio que atenda outra cidade é descartado no filtro, não
coletado "já que estamos aqui".

POR QUE FECHAR. A base tinha anúncio de 41 cidades e 36 delas com menos de
vinte linhas -- Águas de Lindóia com duas, Pardinho com uma. Cauda que não
treina modelo, não enche mapa e não vira produto, e que custa requisição no
site da fonte toda vez. As quinze são as que o índice FipeZAP publica e que o
mapa do produto mostra: cobrir exatamente elas alinha o que coletamos com o que
exibimos.

OS CÓDIGOS IBGE FORAM CONFERIDOS NA API DE LOCALIDADES em 2026-09-13, não
escritos de memória. Eles são a chave do CNEFE, e um código errado baixa o
município errado sem erro nenhum: a tabela sai cheia, com CEPs de outra cidade.

AS COORDENADAS SÃO AS DO MAPA DO PRODUTO. Vêm de `frontend/lib/geo.ts`, que é a
referência visual da tela, e por isso o pino e o dado não podem divergir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Estado(str, Enum):
    """Onde cada cidade está no caminho até virar coleta."""

    COLETANDO = "coletando"
    """Há adapter aprovado e anúncio no lago."""

    INVENTARIANDO = "inventariando"
    """Fontes levantadas, adapter ainda não escrito."""

    A_INVENTARIAR = "a_inventariar"
    """Nada levantado. Nenhuma linha de código antes do inventário."""


@dataclass(frozen=True)
class Cidade:
    nome: str
    """Grafia canônica, com acento. É a chave que a API e a tela usam."""

    uf: str
    cod_ibge: str
    """Chave do CNEFE. Conferido na API de localidades do IBGE."""

    lat: float
    lon: float
    estado: Estado
    dominios: tuple[str, ...] = ()
    """Domínios já inventariados para esta cidade."""

    nota: str = ""

    @property
    def slug(self) -> str:
        """Forma de caminho, a mesma que o lago usa."""
        import unicodedata
        t = unicodedata.normalize("NFKD", self.nome)
        t = "".join(c for c in t if not unicodedata.combining(c))
        return t.lower().replace(" ", "-")

    @property
    def chave(self) -> str:
        """`nome|uf`, como o mapa do frontend indexa."""
        return f"{self.nome}|{self.uf}"


# ---------------------------------------------------------------------------
# As quinze
#
# Ordenadas por prioridade de coleta, e a ordem tem razão: primeiro as que já
# rendem, depois as de maior mercado, por último as menores. Coletar Vitória
# antes do Rio inverteria o retorno do mesmo esforço.
# ---------------------------------------------------------------------------

CIDADES: tuple[Cidade, ...] = (
    # -- já coletadas -------------------------------------------------------
    Cidade("Santos", "SP", "3548500", -23.86771, -46.29055, Estado.COLETANDO,
           ("scimoveissantos.com.br", "casabellaimoveis.com",
            "cferreiraimoveis.com.br", "taguaimoveis.com.br",
            "consultareimoveis.com.br", "spolaorimoveis.com.br",
            "serlamimobiliaria.com.br", "vivareal.com.br", "lopes.com.br"),
           "7.421 anúncios, 46 bairros. Tem modelo de venda e de locação."),
    Cidade("São Paulo", "SP", "3550308", -23.64944, -46.64802, Estado.COLETANDO,
           ("vivareal.com.br", "lopes.com.br"),
           "6.264 anúncios, 819 bairros. Tem modelo de venda; locação tem 2 "
           "linhas e por isso não estima."),
    Cidade("Guarujá", "SP", "3518701", -23.94914, -46.23437, Estado.COLETANDO,
           ("vivareal.com.br",),
           "436 anúncios, vindos de plataforma que atende a região."),

    # -- maior mercado, a inventariar --------------------------------------
    Cidade("Rio de Janeiro", "RJ", "3304557", -22.9254, -43.45765,
           Estado.A_INVENTARIAR, (),
           "Segundo maior mercado do país. Prioridade depois de São Paulo."),
    Cidade("Brasília", "DF", "5300108", -15.78067, -47.79718,
           Estado.A_INVENTARIAR, (),
           "Mercado grande e concentrado; o CNEFE do DF é um município só."),
    Cidade("Fortaleza", "CE", "2304400", -3.78527, -38.5286,
           Estado.A_INVENTARIAR),
    Cidade("Goiânia", "GO", "5208707", -16.64341, -49.27402,
           Estado.A_INVENTARIAR),
    Cidade("Florianópolis", "SC", "4205407", -27.57958, -48.50918,
           Estado.A_INVENTARIAR, (),
           "Entrada para o eixo de SC, que tem quatro cidades na lista."),

    # -- eixo de Santa Catarina --------------------------------------------
    Cidade("Balneário Camboriú", "SC", "4202008", -27.00421, -48.62085,
           Estado.A_INVENTARIAR, (),
           "R$ 15.343/m², o terceiro mais caro da lista. Mercado de alto padrão."),
    Cidade("Itajaí", "SC", "4208203", -26.96787, -48.75202,
           Estado.A_INVENTARIAR),
    Cidade("Itapema", "SC", "4208302", -27.10769, -48.63197,
           Estado.A_INVENTARIAR, (),
           "R$ 15.403/m², o segundo mais caro. Cidade pequena, mercado caro."),

    # -- demais capitais ----------------------------------------------------
    Cidade("Vitória", "ES", "3205309", -20.27895, -40.29648,
           Estado.A_INVENTARIAR, (),
           "R$ 15.650/m², o mais caro da lista."),
    Cidade("Campo Grande", "MS", "5002704", -20.913, -54.24983,
           Estado.A_INVENTARIAR),
    Cidade("São Luís", "MA", "2111300", -2.63099, -44.30717,
           Estado.A_INVENTARIAR),
    Cidade("Aracaju", "SE", "2800308", -10.99654, -37.0953,
           Estado.A_INVENTARIAR, (),
           "R$ 5.826/m², o mais barato da lista."),
)


# ---------------------------------------------------------------------------
# consultas
# ---------------------------------------------------------------------------

POR_SLUG = {c.slug: c for c in CIDADES}
POR_IBGE = {c.cod_ibge: c for c in CIDADES}


def alvo(nome_ou_slug: str) -> Cidade | None:
    """A cidade, por nome ou por slug. `None` para o que está fora da lista."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(nome_ou_slug))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().strip().replace(" ", "-")
    return POR_SLUG.get(t)


def esta_no_alvo(nome_ou_slug: str) -> bool:
    """
    Se a cidade pertence à lista fechada.

    É este o predicado que o coletor consulta antes de gravar. Um anúncio de
    cidade fora da lista é descartado na ingestão -- não filtrado depois, no
    tratamento, onde já teria custado requisição e disco.
    """
    return alvo(nome_ou_slug) is not None


def por_estado(estado: Estado) -> tuple[Cidade, ...]:
    return tuple(c for c in CIDADES if c.estado is estado)


def codigos_ibge() -> tuple[str, ...]:
    """Os municípios que o CNEFE precisa carregar. Exatamente estes."""
    return tuple(c.cod_ibge for c in CIDADES)


def resumo() -> dict:
    return {
        "total": len(CIDADES),
        "coletando": len(por_estado(Estado.COLETANDO)),
        "inventariando": len(por_estado(Estado.INVENTARIANDO)),
        "a_inventariar": len(por_estado(Estado.A_INVENTARIAR)),
        "ufs": sorted({c.uf for c in CIDADES}),
    }
