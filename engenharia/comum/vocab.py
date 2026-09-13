"""
Vocabulário controlado — normalização de valores categóricos.

O §4.1 do plano de refatoração define os vocabulários desde o início; nada os
aplicava. Os adapters gravam o texto cru da fonte, e o resultado medido em
5.342 linhas de 7 domínios:

    property_type   131 valores distintos
    amenities       305 rótulos distintos
    neighborhood    213 valores
    city             45 valores
    state             4 valores  ('SP', 'sp', 'ES', 'PR')

Isto não é ruído: são a mesma categoria escrita de formas diferentes por
plataformas diferentes. `Varanda gourmet` e `Varanda Gourmet` viram duas
features num modelo; `Guaruja` e `Guarujá` viram duas cidades num groupby.

**Este módulo pertence à camada de tratamento, não à de coleta.** A camada
crua registra o que a fonte disse. Normalizar ali apagaria a evidência e
impediria reprocessar com regra nova.

Princípio de projeto: **dobrar por chave, mapear por sinônimo.** Variação de
caixa e acento é resolvida por uma função de dobra; equivalência genuína
(`Aceita pets` = `Aceita Animais`) exige tabela explícita, porque nenhuma
heurística acerta isso sem inventar.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

# --------------------------------------------------------------------------
# Dobra — a chave de comparação
# --------------------------------------------------------------------------

def dobra(texto) -> str:
    """
    Forma canônica de comparação: sem acento, minúscula, espaço colapsado,
    pontuação de borda removida.

    `Área de Serviço`, `Área de serviço` e `AREA DE SERVICO` dobram para a
    mesma chave. É o que resolve a maior parte dos 305 rótulos de amenidade
    sem nenhuma tabela.
    """
    if texto is None:
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("/", " ").replace("-", " ")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------
# UF
# --------------------------------------------------------------------------

def normaliza_uf(valor) -> str | None:
    """`sp` e `SP` são a mesma UF. 506 linhas gravavam a forma minúscula."""
    if not valor:
        return None
    uf = str(valor).strip().upper()
    return uf if re.fullmatch(r"[A-Z]{2}", uf) else None


# --------------------------------------------------------------------------
# Município
# --------------------------------------------------------------------------
# Chave dobrada -> forma oficial acentuada. Construída a partir dos 45 valores
# observados; a forma canônica é a grafia oficial do município.
MUNICIPIOS = {
    "santos": "Santos",
    "sao vicente": "São Vicente",
    "guaruja": "Guarujá",
    "praia grande": "Praia Grande",
    "cubatao": "Cubatão",
    "bertioga": "Bertioga",
    "mongagua": "Mongaguá",
    "itanhaem": "Itanhaém",
    "peruibe": "Peruíbe",
    "sao paulo": "São Paulo",
    "sao sebastiao": "São Sebastião",
    "ilhabela": "Ilhabela",
    "ubatuba": "Ubatuba",
    "caraguatatuba": "Caraguatatuba",
    "santo andre": "Santo André",
    "sao bernardo do campo": "São Bernardo do Campo",
    "guarulhos": "Guarulhos",
    "osasco": "Osasco",
    "cotia": "Cotia",
    "embu das artes": "Embu das Artes",
    "mogi das cruzes": "Mogi das Cruzes",
    "santa isabel": "Santa Isabel",
    "atibaia": "Atibaia",
    "jaguariuna": "Jaguariúna",
    "campinas": "Campinas",
    "sorocaba": "Sorocaba",
    "votorantim": "Votorantim",
    "salto": "Salto",
    "itu": "Itu",
    "boituva": "Boituva",
    "ibiuna": "Ibiúna",
    "igarata": "Igaratá",
    "serra negra": "Serra Negra",
    "aguas de lindoia": "Águas de Lindóia",
    "cananeia": "Cananéia",
    "pardinho": "Pardinho",
    "curitiba": "Curitiba",
    "aracruz": "Aracruz",
}

# Valores que aparecem no campo `city` e NÃO são município. Registrados para
# não virarem cidade por acidente.
NAO_E_MUNICIPIO = {"ribeiro dos santos"}


def normaliza_municipio(valor) -> str | None:
    """
    Grafia oficial do município.

    Sem a normalização, `Guaruja` (151 linhas, do adapter que deriva a cidade
    do slug da URL) e `Guarujá` (286) são duas cidades num `groupby`.
    """
    chave = dobra(valor)
    if not chave or chave in NAO_E_MUNICIPIO:
        return None
    if chave in MUNICIPIOS:
        return MUNICIPIOS[chave]
    # Desconhecido: devolve com iniciais maiúsculas, preservando a acentuação
    # original. Melhor que descartar -- o município pode ser real e novo.
    return re.sub(r"\s+", " ", str(valor).strip()).title()


# --------------------------------------------------------------------------
# Bairro
# --------------------------------------------------------------------------
# Preposições que não recebem maiúscula na grafia de topônimo pt-BR.
_MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def _titula(valor) -> str | None:
    """`PONTA DA PRAIA` -> `Ponta da Praia`. Só ajusta caixa."""
    if not valor or not str(valor).strip():
        return None
    palavras = re.sub(r"\s+", " ", str(valor).strip()).split()
    saida = []
    for i, p in enumerate(palavras):
        pl = p.lower()
        saida.append(pl if (i > 0 and pl in _MINUSCULAS) else p[:1].upper() + p[1:].lower())
    return " ".join(saida)


def _acentos(texto: str) -> int:
    return sum(1 for c in texto if ord(c) > 127)


def mapa_canonico(valores) -> dict[str, str]:
    """
    Constrói {chave dobrada -> grafia canônica} a partir do próprio corpus.

    Não existe tabela oficial de bairro: são 213 valores e a lista muda por
    cidade. Mas existe um critério objetivo para escolher entre grafias da
    MESMA chave, e ele não é frequência.

    **Prefere a grafia com mais acentos.** `Embaré` sobre `Embare`,
    `Boqueirão` sobre `Boqueirao`. Acento é informação; a forma sem acento é
    perda, e vem de uma única plataforma, que deriva o bairro do slug da URL.

    Frequência seria frágil: bastaria essa plataforma crescer para a grafia
    empobrecida virar canônica. O critério de acento é estável.

    Desempate: frequência, depois ordem alfabética -- para o mapa ser
    determinístico entre execuções.
    """
    grupos: dict[str, Counter] = {}
    for v in valores:
        t = _titula(v)
        if t:
            grupos.setdefault(dobra(t), Counter())[t] += 1
    mapa = {}
    for chave, formas in grupos.items():
        mapa[chave] = max(formas, key=lambda f: (_acentos(f), formas[f], f))
    return mapa


def normaliza_bairro(valor, mapa: dict[str, str] | None = None) -> str | None:
    """
    Título com preposições em minúscula, e grafia unificada quando há mapa.

    Sem o mapa, `Embare` e `Embaré` continuam sendo dois bairros -- foi o que
    aconteceu em 14 chaves das 213 medidas.
    """
    t = _titula(valor)
    if t is None:
        return None
    return (mapa or {}).get(dobra(t), t)


# --------------------------------------------------------------------------
# Tipo de imóvel
# --------------------------------------------------------------------------
# Contaminação medida no campo (defeito D-12): o adapter de uma plataforma
# derivava o tipo do título do anúncio, e o título carrega transação, metragem
# e texto de marketing.
_CONTAMINACAO = re.compile(
    r"\s*(para\s+(alugar|locacao|venda|venda\s+e\s+locacao)"
    r"|a\s+venda|de\s+locacao|venda\s+e\s+locacao"
    r"|\d+\s*m2?\b|proxim[oa]\s+d[ao].*|frente\s+mar)\s*",
    re.I)

# Ordem importa: o primeiro padrão que casar vence. `casa de condominio` antes
# de `casa`; `sobre loja` antes de `loja`.
_TIPOS = [
    ("apartamento",   r"\bapartamento|\bapto\b|\bkitnet|\bkitchenette|\bconjugado"),
    ("cobertura",     r"\bcobertura"),
    ("flat",          r"\bflat\b"),
    ("studio",        r"\bstudio\b|\bstúdio"),
    ("sobrado",       r"\bsobrado"),
    ("casa",          r"\bcasa\b|\bcasa\s|\bvillage\b|\bsobre\s?loja"),
    ("terreno",       r"\bterreno|\blote\b|\barea\b"),
    ("chacara",       r"\bchacara|\bsitio|\bfazenda|\brancho"),
    ("sala",          r"\bsala\b|\bsala\s|\bconjunto|\blaje\b|\bandar\s+corporativo|\bescritorio"),
    ("loja",          r"\bloja|\bponto\s+comercial|\bsalao\b|\bcomercio\b"),
    ("galpao",        r"\bgalpao|\barmazem|\bdeposito\b"),
    ("predio",        r"\bpredio|\bedificio\b"),
    ("garagem",       r"\bgaragem\b|\bbox\b|\bvaga\b"),
]
_TIPOS_COMPILADOS = [(nome, re.compile(pat)) for nome, pat in _TIPOS]

# `kitnet` casaria em `apartamento` pela regra acima; é tratado antes para não
# perder a distinção, que é relevante para preço.
_ESPECIFICOS = [
    ("kitnet", re.compile(r"\bkitnet|\bkitchenette|\bconjugado")),
]

TIPOS_CANONICOS = ("apartamento", "kitnet", "cobertura", "flat", "studio",
                   "sobrado", "casa", "terreno", "chacara", "sala", "loja",
                   "galpao", "predio", "garagem")


def normaliza_tipo(valor) -> str | None:
    """
    Texto livre do anúncio -> um de 14 tipos canônicos.

    Exemplos reais que esta função precisa resolver:

        'Apartamento Residencial para venda e locação' -> apartamento
        'Sala Comercial para locação'                  -> sala
        'Apartamento Garden 94 m²'                     -> apartamento
        'Casa De Condominio'                           -> casa
        'O Espaço Ideal para o Sucesso do Seu Negócio' -> None
    """
    chave = dobra(_CONTAMINACAO.sub(" ", str(valor or "")))
    if not chave:
        return None
    for nome, padrao in _ESPECIFICOS:
        if padrao.search(chave):
            return nome
    for nome, padrao in _TIPOS_COMPILADOS:
        if padrao.search(chave):
            return nome
    return None


def e_residencial(tipo_canonico) -> bool | None:
    """Segmento do imóvel. `None` quando o tipo não determina."""
    if tipo_canonico is None:
        return None
    if tipo_canonico in ("apartamento", "kitnet", "cobertura", "flat", "studio",
                         "sobrado", "casa", "chacara"):
        return True
    if tipo_canonico in ("sala", "loja", "galpao", "predio", "garagem"):
        return False
    # `terreno` não determina segmento: o mesmo lote pode ser anunciado para
    # uso residencial ou comercial, e a fonte raramente diz qual. Devolver
    # True ou False aqui seria inventar.
    return None


# --------------------------------------------------------------------------
# Amenidades
# --------------------------------------------------------------------------
# Sinônimos genuínos: rótulos diferentes para a mesma coisa. Só entram aqui os
# casos que a dobra NÃO resolve -- variação de caixa e acento já é tratada.
#
# Conceitos próximos mas distintos ficam separados de propósito:
# `Vista para o Mar` != `Frente para o mar` != `Beira-mar` -- há gradação de
# valor entre eles, e fundi-los apagaria sinal.
SINONIMOS = {
    "aceita pet": "aceita animais",
    "aceita pets": "aceita animais",
    "aceita animais": "aceita animais",
    "permite animais": "aceita animais",
    "wc empregada": "dependencia de empregada",
    "wc de empregada": "dependencia de empregada",
    "dependencia de empregada": "dependencia de empregada",
    "dormitorio de empregada": "dependencia de empregada",
    "quarto de empregada": "dependencia de empregada",
    "dce": "dependencia de empregada",
    "armarios na cozinha": "armario na cozinha",
    "armario na cozinha": "armario na cozinha",
    "cozinha planejada": "armario na cozinha",
    "armario embutido no quarto": "armario no quarto",
    "armario no quarto": "armario no quarto",
    "armarios no quarto": "armario no quarto",
    "ar condicionado": "ar condicionado",
    "portao eletronico": "portao eletronico",
    "salao de festas": "salao de festas",
    "salao de festas gourmet": "salao de festas",
    "sala de ginastica": "academia",
    "academia": "academia",
    "fitness": "academia",
    "piscina adulto": "piscina",
    "piscina": "piscina",
    "piscina aquecida": "piscina aquecida",
    "quadra poliesportiva": "quadra esportiva",
    "quadra esportiva": "quadra esportiva",
    "varanda sacada": "varanda",
    "sacada": "varanda",
    "varanda": "varanda",
    "varanda gourmet": "varanda gourmet",
    "seguranca 24 horas": "portaria 24 horas",
    "portaria 24 horas": "portaria 24 horas",
    "vigia": "vigia",
    "guarita": "guarita",
    "tv a cabo": "tv a cabo",
    "acesso para deficientes": "acessibilidade",
    "acessibilidade": "acessibilidade",
    "circuito interno de tv": "circuito interno de tv",
    "cftv": "circuito interno de tv",
    "gas canalizado": "gas canalizado",
    "central de gas": "gas canalizado",
    "condominio fechado": "condominio fechado",
}


def normaliza_amenidade(valor) -> str | None:
    """Um rótulo cru -> chave canônica, ou `None` se vazio."""
    chave = dobra(valor)
    if not chave or len(chave) < 2:
        return None
    return SINONIMOS.get(chave, chave)


def normaliza_amenidades(lista) -> list[str]:
    """
    Lista crua -> lista canônica ordenada, sem duplicatas.

    A ordenação é deliberada: sem ela, duas linhas com o mesmo conjunto de
    amenidades produzem listas diferentes conforme a ordem em que a fonte as
    emitiu, e qualquer comparação de conjunto fica dependente de ordem.
    """
    if lista is None:
        return []
    saida = set()
    for item in lista:
        c = normaliza_amenidade(item)
        if c:
            saida.add(c)
    return sorted(saida)
