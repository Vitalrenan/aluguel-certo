"""
Extração de amenidades a partir do texto livre da descrição.

A descrição do anúncio carrega atributos que não estão em coluna nenhuma. Medido
em 3.789 descrições da partição de 2026-08-29: o texto menciona elevador,
garagem, mobília, vista para o mar e lazer em massa, e parte disso não aparece
na lista `amenities` que a fonte fornece.

**Negação é tratada, e é o ponto mais delicado deste módulo.** Afirmar que um
imóvel NÃO tem garagem é informação de preço tão relevante quanto afirmar que
tem — e um regex ingênuo extrairia `garagem` de "não possui vaga de garagem".

Três padrões reais medidos no corpus, cada um exigindo tratamento próprio:

    "não possui vaga de garagem, porém no edifício tem um prédio de garagens"
        -> o escopo da negação TERMINA em "porém". Sem isso, a segunda
           garagem também seria negada.

    "não possui garagem, portaria nem bicicletário"
        -> "nem" ESTENDE a negação para os três termos.

    "sem necessidade de manobra" · "sem sair de casa" · "sem cobrança de condomínio"
        -> `sem` aparece em 10,6% das descrições e quase nunca nega amenidade.
           Só conta quando o termo vem logo depois e sem palavra-barreira.

Frequência dos marcadores no corpus (3.789 descrições):

    não possui   35    não tem       20    não há        3
    não aceita    3    não conta com  2    não inclui    1
    sem         402  <- a grande maioria falso positivo
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Normalização
# --------------------------------------------------------------------------

def dobra(texto) -> str:
    """Sem acento, minúscula, espaço colapsado — a forma usada para casar."""
    if texto is None:
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower())


# --------------------------------------------------------------------------
# Vocabulário — padrão de texto -> rótulo canônico
# --------------------------------------------------------------------------
# Os rótulos de saída usam a grafia mais frequente da coluna `amenities`, para
# que o que vem do texto e o que vem da fonte fiquem no mesmo vocabulário.
#
# Os padrões cobrem a forma como o atributo aparece em PROSA, que raramente é
# igual ao rótulo: a fonte diz "Elevador Social"; o texto diz "prédio com
# elevadores".
VOCABULARIO: list[tuple[str, str]] = [
    # --- estrutura do prédio ---
    ("Elevador",                r"\belevador(es)?\b"),
    ("Portaria 24 horas",       r"\bportaria\s*(de\s*)?24\s*(h\b|horas?\b)|\bporteiro\s*24"),
    ("Portaria",                r"\bportaria\b|\bporteiro\b|\brecepcao\s+24"),
    ("Guarita",                 r"\bguarita\b"),
    ("Zelador",                 r"\bzelador\b"),
    ("Vigia",                   r"\bvigia\b|\bvigilancia\s+24"),
    ("Circuito Interno de TV",  r"\bcftv\b|\bcircuito\s+interno\s+de\s+tv\b|\bcameras?\s+de\s+(monitoramento|seguranca)\b"),
    ("Interfone",               r"\binterfone\b"),
    ("Portão Eletrônico",       r"\bportao\s+eletronico\b|\bporteiro\s+eletronico\b"),
    ("Alarme",                  r"\balarme\b"),
    ("Cerca Elétrica",          r"\bcerca\s+eletrica\b"),
    ("Bicicletário",            r"\bbicicletario\b|\bpranchario\b"),
    ("Acessibilidade",          r"\bacessibilidade\b|\bacesso\s+para\s+deficientes\b|\brampa(s)?\s+de\s+acesso\b"),
    ("Condomínio fechado",      r"\bcondominio\s+fechado\b"),
    ("Hall de Entrada",         r"\bhall\s+de\s+entrada\b"),
    ("Gás Canalizado",          r"\bgas\s+(canalizado|encanado)\b|\bcentral\s+de\s+gas\b"),

    # --- lazer ---
    ("Piscina aquecida",        r"\bpiscina\s+aquecida\b"),
    ("Piscina",                 r"\bpiscina\b"),
    ("Churrasqueira",           r"\bchurrasqueira\b"),
    ("Salão de Festas",         r"\bsalao\s+de\s+festas?\b"),
    ("Salão de Jogos",          r"\bsalao\s+de\s+jogos?\b|\bsala\s+de\s+jogos\b"),
    ("Academia",                r"\bacademia\b|\bsala\s+de\s+ginastica\b|\bfitness\b|\bespaco\s+fitness\b"),
    ("Sauna",                   r"\bsauna\b"),
    ("Playground",             r"\bplayground\b|\bparquinho\b"),
    ("Brinquedoteca",           r"\bbrinquedoteca\b|\bespaco\s+kids\b"),
    ("Quadra Esportiva",        r"\bquadra\s+(poli)?esportiva\b|\bquadra\s+de\s+(tenis|squash|beach)\b"),
    ("Espaço Gourmet",          r"\bespaco\s+gourmet\b"),
    ("Varanda Gourmet",         r"\bvaranda\s+gourmet\b"),
    ("Hidromassagem",           r"\bhidromassagem\b|\bofuro\b"),
    ("Solarium",                r"\bsolarium\b|\bsolario\b"),
    ("Sala de Massagem",        r"\bsala\s+de\s+massagem\b"),
    ("Home Cinema",             r"\bhome\s+(cinema|theater)\b|\bcinema\b"),
    ("Coworking",               r"\bcoworking\b|\bespaco\s+de\s+trabalho\b"),
    ("Lavanderia",              r"\blavanderia\b"),

    # --- garagem ---
    ("Garagem",                 r"\bvaga(s)?\s+(de\s+|na\s+|para\s+)?garagem\b|\bgaragem\b|\bvaga(s)?\s+de\s+auto\b"),
    ("Garagem Coberta",         r"\bgaragem\s+coberta\b|\bvaga\s+coberta\b"),
    ("Garagem Demarcada",       r"\bgaragem\s+demarcada\b|\bvaga\s+demarcada\b"),
    ("Garagem Privativa",       r"\bgaragem\s+privativa\b|\bvaga\s+privativa\b"),

    # --- interior ---
    ("Mobiliado",               r"\bmobiliad[oa]\b|\bsemi[\s-]?mobiliad[oa]\b|\bcom\s+moveis\s+planejados\b"),
    ("Ar-Condicionado",         r"\bar[\s-]?condicionado\b|\bar\s+cond\b|\bsplit\b"),
    ("Armários na Cozinha",     r"\bcozinha\s+(planejada|com\s+armarios)\b|\barmarios?\s+(na|de)\s+cozinha\b"),
    ("Armário no Quarto",       r"\barmarios?\s+(embutidos?\s+)?(no|nos)\s+(quarto|dormitorio)s?\b|\bdormitorios?\s+planejados?\b"),
    ("Armário no Banheiro",     r"\barmario\s+no\s+banheiro\b|\bgabinete\s+no\s+banheiro\b"),
    ("Closet",                  r"\bcloset\b"),
    ("Lavabo",                  r"\blavabo\b"),
    ("Despensa",                r"\bdespensa\b|\bdispensa\b"),
    ("Escritório",              r"\bescritorio\b|\bhome\s+office\b"),
    ("Copa",                    r"\bcopa\b"),
    ("Área de Serviço",         r"\barea\s+de\s+servico\b"),
    ("Entrada de Serviço",      r"\bentrada\s+de\s+servico\b"),
    ("Dependência de Empregada", r"\bdependencia\s+(completa\s+)?de\s+empregada\b|\bdce\b|\bwc\s+(de\s+)?empregada\b|\bdormitorio\s+de\s+empregada\b|\bquarto\s+de\s+empregada\b"),
    ("Varanda",                 r"\bvaranda\b|\bsacada\b"),
    ("Terraço",                 r"\bterraco\b"),
    ("Quintal",                 r"\bquintal\b"),
    ("Jardim",                  r"\bjardim\b(?!\s+(virginia|pernambuco|acapulco|das|dos|de\s+[A-Z]))"),
    ("Box",                     r"\bbox\s+(blindex|de\s+vidro|simples)\b"),
    ("Lareira",                 r"\blareira\b"),
    ("Mezanino",                r"\bmezanino\b"),
    ("Depósito",                r"\bdeposito\b"),

    # --- vista e orientação ---
    ("Frente para o Mar",       r"\bfrente\s+(para\s+o\s+|pro\s+)?mar\b|\bpe\s+na\s+areia\b|\bbeira[\s-]?mar\b"),
    ("Vista para o Mar",        r"\bvista\s+(para\s+o\s+|pro\s+|do\s+)?mar\b|\bvista\s+mar\b"),
    ("Sol da Manhã",            r"\bsol\s+da\s+manha\b|\bface\s+norte\b|\bsol\s+matinal\b"),
    ("Vista Livre",             r"\bvista\s+livre\b|\bvista\s+panoramica\b|\bvista\s+desimpedida\b"),

    # --- condição ---
    ("Reformado",               r"\breformad[oa]\b|\btodo\s+reformado\b|\brecem\s+reformad[oa]\b"),
    ("Novo",                    r"\bapartamento\s+novo\b|\bpredio\s+novo\b|\bimovel\s+novo\b|\bnunca\s+habitado\b"),
    ("Aceita Animais",          r"\baceita\s+(pet|pets|animai?s?)\b|\bpet\s+friendly\b|\bpermite\s+animais\b"),
    ("Aceita Financiamento",    r"\baceita\s+financiamento\b|\baceita\s+fgts\b"),
    ("Aceita Permuta",          r"\baceita\s+permuta\b"),
]

_COMPILADO = [(rotulo, re.compile(padrao)) for rotulo, padrao in VOCABULARIO]


# --------------------------------------------------------------------------
# Negação
# --------------------------------------------------------------------------
# Marcadores fortes: quando aparecem, negam tudo até o fim do escopo.
_NEGACAO_FORTE = re.compile(
    r"\bnao\s+(tem|possui|ha|contem|conta\s+com|dispoe|oferece|inclui|aceita|e\s+)"
    r"|\bnenhum[ao]?\b|\bausencia\s+de\b|\bisento\s+de\b")

# `sem` é fraco: 402 descrições o contêm e a maioria não nega amenidade
# ("sem necessidade de manobra", "sem sair de casa"). Só vale quando o termo
# vem logo em seguida e nenhuma palavra-barreira aparece no meio.
_SEM = re.compile(r"\bsem\b")
_BARREIRA_SEM = re.compile(
    r"\b(necessidade|sair|cobranca|custo|taxa|compromisso|burocracia|obras?|"
    r"reforma|preocupacao|complicacao|precisar|abrir\s+mao|falar|pagar)\b")
# `sem` tem escopo CURTO e a vírgula o encerra. Medido:
# "prédio com elevadores, sem garagem, portaria 24 horas" -- sem o corte na
# vírgula, a portaria também seria negada.
_FIM_SEM = re.compile(r"[.;,!?]|\b(mas|porem|com|e)\b")
_JANELA_SEM = 40

# Teto de palavras da negação forte, calibrado contra o corpus:
#   "não possui garagem, portaria nem bicicletário"  -> 4 palavras, precisa caber
#   "Não tem lazer ou salão de festas Portaria..."   -> Portaria na 6a, não deve
# Cinco separa os dois casos sem exigir pontuação, que estes textos não têm.
_MAX_PALAVRAS_NEGACAO = 5

# O escopo da negação forte termina aqui. Medido: "não possui vaga de garagem,
# PORÉM no edifício tem um prédio de garagens" — sem este corte, a segunda
# menção seria negada também.
_FIM_ESCOPO = re.compile(
    r"[.;!?]"
    r"|\b(mas|porem|entretanto|contudo|todavia|embora|no\s+entanto)\b"
    r"|\b(com|possui|tem|conta\s+com|dispoe|oferece|excelente|otim[oa]|lind[oa]|"
    r"ampl[oa]|maravilhos[oa]|contendo|distribuidos?)\b")


def _escopos_negados(texto: str) -> list[tuple[int, int]]:
    """
    Intervalos [início, fim) do texto em que uma negação está ativa.

    Negação forte vale do marcador até o próximo delimitador de frase OU
    adversativa. `nem` não interrompe — ao contrário, é parte do escopo:
    "não possui garagem, portaria nem bicicletário" nega os três.
    """
    escopos = []
    for m in _NEGACAO_FORTE.finditer(texto):
        fim = _FIM_ESCOPO.search(texto, m.end())
        limite = fim.start() if fim else len(texto)
        # Teto de palavras: sem ele, uma negação sem pontuação depois negaria
        # o parágrafo inteiro.
        palavras = list(re.finditer(r"\S+", texto[m.end():limite]))
        if len(palavras) > _MAX_PALAVRAS_NEGACAO:
            limite = m.end() + palavras[_MAX_PALAVRAS_NEGACAO].start()
        # O escopo começa no INÍCIO do marcador, não no fim: rótulos que contêm
        # o verbo ("Aceita Animais" em "não aceita pets") precisam cair dentro.
        escopos.append((m.start(), limite))
    for m in _SEM.finditer(texto):
        limite = min(m.end() + _JANELA_SEM, len(texto))
        corte = _FIM_SEM.search(texto, m.end(), limite)
        if corte:
            limite = corte.start()
        if _BARREIRA_SEM.search(texto[m.end():limite]):
            continue
        escopos.append((m.end(), limite))
    return escopos


def _negado(pos: int, escopos: list[tuple[int, int]]) -> bool:
    return any(ini <= pos < fim for ini, fim in escopos)


# --------------------------------------------------------------------------
# Extração
# --------------------------------------------------------------------------

def extrai(descricao) -> tuple[list[str], list[str]]:
    """
    Texto -> (amenidades afirmadas, amenidades negadas).

    Cada rótulo aparece no máximo uma vez em cada lista, mesmo que o texto o
    mencione várias vezes. Um rótulo mencionado nas duas condições no mesmo
    texto ("não tem elevador... prédio com elevador social") fica nas duas
    listas: a contradição é do anúncio, e resolvê-la aqui seria inventar.
    """
    if not isinstance(descricao, str) or not descricao.strip():
        return [], []

    texto = dobra(descricao)
    escopos = _escopos_negados(texto)

    positivos: list[str] = []
    negativos: list[str] = []
    for rotulo, padrao in _COMPILADO:
        pos, neg = False, False
        for m in padrao.finditer(texto):
            if _negado(m.start(), escopos):
                neg = True
            else:
                pos = True
        if pos and rotulo not in positivos:
            positivos.append(rotulo)
        if neg and rotulo not in negativos:
            negativos.append(rotulo)
    return positivos, negativos


def enriquece(amenidades_atuais, descricao) -> tuple[list[str], list[str]]:
    """
    Acrescenta o que veio do texto à lista que já existe.

    **Duplicatas são permitidas por decisão explícita.** Se a fonte já listou
    `Piscina` e o texto também a menciona, a lista final tem as duas —
    a repetição é sinal de que fonte e texto concordam, e distingui-la de uma
    menção única é informação que a deduplicação apagaria.

    A ordem preserva a origem: primeiro o que veio da fonte, depois o que veio
    do texto.
    """
    base = list(amenidades_atuais) if amenidades_atuais is not None else []
    positivos, negativos = extrai(descricao)
    return base + positivos, negativos
