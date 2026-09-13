"""
O formulário da calculadora — definição única do modelo ajustado.

Este módulo é a fronteira entre a medição e o produto. Ele responde a duas
perguntas, e as duas têm de ter a MESMA resposta no treino e na inferência:

    1. quais colunas o modelo consome;
    2. como a resposta de um usuário vira o valor de cada coluna.

Escrito assim porque o modo de falha caro do projeto é o silencioso: se o
serviço montar `fam_piscina` de um jeito e o treino de outro, nada levanta --
sai uma previsão plausível e errada. Por isso `treina_final.py` e `serving/`
importam daqui, e nenhum dos dois define coluna por conta própria.

PROCEDÊNCIA DAS ESCOLHAS. Nada aqui é intuição:

  - as 13 perguntas de atributo e os dois opcionais vêm de MEDICOES §27 e da
    decisão D-035 -- corte de ΔRMSE >= 1.000 R$ contra teto de placebo de 64 R$,
    confirmado em ablação pareada de 25 folds;
  - as 6 famílias de amenidade vêm de MEDICOES §28 -- valem -1.891 R$ em 25/25
    folds quando somadas ao formulário de 13;
  - `em_obra`, `portaria` e as 10 famílias reprovadas ficam de fora por medição,
    não por gosto.

O QUE ESTA IMPLEMENTAÇÃO RECONSTRÓI. Os scripts do §27 e do §28 não ficaram
salvos. As definições de família abaixo foram remontadas e conferidas contra as
prevalências publicadas em §28, no mesmo recorte (venda_ate800k, n=6.974):
as seis batem na segunda casa decimal. Ver `tests/test_formulario.py`.

DUAS CONTAGENS QUE DIVERGEM DO DOCUMENTO, declaradas:

  - `Ajustes_calculadora_app.md` diz "de 13 para 19 perguntas de atributo".
    São **18**: a tabela lista "O condomínio tem piscina?" duas vezes (#14
    `amen_piscina` e #16 `fam_piscina`). É uma pergunta ao usuário que alimenta
    duas colunas -- as duas entram no modelo, mas o usuário responde uma vez.
  - o §28 escreve `amen_sem_elevador` a 0,44% e `nega_elevador` a 0,72%. Medido
    no recorte, são 0,83% e 1,48%. A prevalência consolidada (2,08%) e o "maior
    membro" (1,48%) da tabela do §28 estão certos; a prosa não.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from comum.ml import dados, regras_texto
from comum.ml.avaliacao import OPCIONAIS
from comum import schema, vocab

__all__ = ["FAMILIAS", "PERGUNTAS", "COLUNAS", "CATEGORICAS", "BOOLEANAS",
           "NUMERICAS", "OPCIONAIS", "INVERTIDAS", "MontagemInvalida",
           "monta_base", "matriz", "linha_do_usuario", "campos_ausentes",
           "ressalvas", "normaliza_tipo", "rotulo", "ROTULOS"]


# --------------------------------------------------------------------------
# As famílias de amenidade — MEDICOES §28
#
# Cada família é o OR booleano dos membros. Os membros vivem no P02: a poda do
# P03 (suporte >= 30) descarta justamente as colunas raras, que são as que a
# consolidação existe para juntar. `fam_piscina` teria 6 membros em vez de 15
# se fosse montada do P03.
#
# `fam_sem_elevador` é o caso que justifica a técnica: separadas, as duas
# colunas não têm prevalência para o modelo aprender; juntas dão 2,08% e valem
# 1.018 R$ com 10/10 folds.
# --------------------------------------------------------------------------
FAMILIAS: dict[str, tuple[str, ...]] = {
    "fam_sem_elevador": ("amen_sem_elevador", "nega_elevador"),
    "fam_piscina": (
        "amen_piscina", "amen_piscina_aquecida", "amen_piscina_coberta",
        "amen_piscina_infantil", "amen_piscina_privativa", "amen_piscina_adulto",
        "amen_piscina_adulto_coberta", "amen_piscina_adulto_descoberta",
        "amen_piscina_com_cascata", "amen_piscina_com_hidromassagem",
        "amen_piscina_com_raia", "amen_piscina_da_cobertura",
        "amen_piscina_descoberta", "amen_piscina_infantil_coberta",
        "amen_piscina_infantil_descoberta",
    ),
    "fam_sauna": ("amen_sauna", "amen_sauna_seca", "amen_sauna_umida"),
    "fam_ginastica": ("amen_academia", "amen_academia_de_ginastica",
                      "amen_sala_de_ginastica"),
    "fam_quintal": ("amen_quintal",),
    "fam_ar_condicionado": ("amen_ar_condicionado",),
}

# Prevalência publicada em MEDICOES §28, para o teste de reconstrução conferir.
PREVALENCIA_MEDIDA: dict[str, float] = {
    "fam_sem_elevador": 2.08, "fam_piscina": 21.61, "fam_sauna": 4.95,
    "fam_ginastica": 20.56, "fam_quintal": 14.21, "fam_ar_condicionado": 9.25,
}


# --------------------------------------------------------------------------
# As perguntas
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Pergunta:
    """
    Uma pergunta do formulário e as colunas que ela alimenta.

    `colunas` é plural porque "o condomínio tem piscina?" alimenta duas --
    `amen_piscina` e `fam_piscina` são colunas diferentes no treino (21,05% e
    21,61% de prevalência) e as duas entram no braço vencedor do §28.
    """
    api: str                       # nome do campo no contrato HTTP
    rotulo: str                    # o que se pergunta ao usuário
    tipo: str                      # categorica | inteiro | real | booleana
    colunas: tuple[str, ...]
    obrigatoria: bool = True
    faixa: tuple[float, float] | None = None
    delta_rmse: int | None = None  # o que a medição pagou por ela, em R$
    fonte: str = "P03"             # de onde a coluna vem no treino
    nota: str = ""


# Faixas: LIDAS de `comum.schema`, não copiadas. Arquitetura §6 é explícita --
# "o serviço usa as mesmas faixas, não uma cópia em Pydantic". Duas tabelas de
# faixa divergem no primeiro ajuste, e a divergência não levanta exceção: o
# serviço aceita o que a coleta rejeita, ou o contrário.
#
# Fora da faixa é 422, não anulação. Na coleta, anular é certo -- a alternativa
# é perder a linha inteira. Na calculadora o usuário está diante da tela para
# corrigir: `area_m2 = 8000` é dedo escorregado, e estimar em cima disso é pior
# que devolver erro.
_FX = dict(schema.FAIXAS_FISICAS) | dict(schema.FAIXAS)
PERGUNTAS: tuple[Pergunta, ...] = (
    Pergunta("transacao", "Comprar ou alugar?", "categorica",
             ("transaction_type",),
             nota="escolhe o modelo; constante dentro de um segmento"),
    Pergunta("cidade", "Cidade", "categorica", ("city",), delta_rmse=4_405),
    Pergunta("bairro", "Bairro", "categorica", ("neighborhood",),
             delta_rmse=23_441,
             nota="variável mais forte da base depois da área"),
    Pergunta("tipo", "Tipo de imóvel", "categorica", ("property_type",),
             delta_rmse=1_966),
    Pergunta("area_m2", "Área útil (m²)", "real", ("area_m2",),
             faixa=_FX["area_m2"], delta_rmse=32_530),
    Pergunta("dormitorios", "Dormitórios", "inteiro", ("bedrooms",),
             faixa=_FX["bedrooms"], delta_rmse=2_171),
    Pergunta("banheiros", "Banheiros", "inteiro", ("bathrooms",),
             faixa=_FX["bathrooms"], delta_rmse=4_442),
    Pergunta("suites", "Suítes", "inteiro", ("suites",), faixa=_FX["suites"],
             delta_rmse=8_757,
             nota="terceira variável mais forte da base"),
    Pergunta("vagas", "Vagas de garagem", "inteiro", ("parking_spots",),
             faixa=_FX["parking_spots"], delta_rmse=4_647),
    Pergunta("vaga_tipo", "A vaga é privativa, coletiva ou rotativa?",
             "categorica", ("rx_vaga_tipo",), delta_rmse=1_007,
             fonte="P01/regras_texto"),
    Pergunta("andar", "Andar", "inteiro", ("floor_level",), faixa=_FX["floor_level"],
             obrigatoria=False, delta_rmse=2_399,
             nota="38% de preenchimento no treino e mesmo assim -1.702 R$"),
    Pergunta("varanda", "Tem varanda?", "booleana", ("amen_varanda",),
             obrigatoria=False, delta_rmse=1_208, fonte="P03/amenities"),
    Pergunta("vista", "Vista", "categorica", ("rx_vista",), delta_rmse=1_011,
             fonte="P01/regras_texto"),
    Pergunta("piscina", "O condomínio tem piscina?", "booleana",
             ("amen_piscina", "fam_piscina"), obrigatoria=False, delta_rmse=1_276,
             fonte="P03/amenities + P02/família",
             nota="uma pergunta, duas colunas: §27 mediu a coluna, §28 a família"),
    Pergunta("elevador", "O prédio tem elevador?", "booleana",
             ("fam_sem_elevador",), obrigatoria=False, delta_rmse=1_018,
             fonte="P02/família",
             nota="coluna invertida: mede a AUSÊNCIA declarada no anúncio"),
    Pergunta("sauna", "Tem sauna?", "booleana", ("fam_sauna",),
             obrigatoria=False, delta_rmse=696, fonte="P02/família"),
    Pergunta("academia", "Tem academia ou sala de ginástica?", "booleana",
             ("fam_ginastica",), obrigatoria=False, delta_rmse=603,
             fonte="P02/família"),
    Pergunta("quintal", "Tem quintal?", "booleana", ("fam_quintal",),
             obrigatoria=False, delta_rmse=507, fonte="P02/família"),
    Pergunta("ar_condicionado", "Tem ar-condicionado?", "booleana",
             ("fam_ar_condicionado",), obrigatoria=False, delta_rmse=486,
             fonte="P02/família"),
    Pergunta("condominio", "Valor do condomínio (mensal)", "real",
             ("condo_fee",), obrigatoria=False, faixa=_FX["condo_fee"],
             delta_rmse=7_863,
             nota="opcional; informar os dois opcionais vale -5.404 R$"),
    Pergunta("iptu", "Valor do IPTU", "real", ("iptu_tax",),
             obrigatoria=False, faixa=_FX["iptu_tax"], delta_rmse=8_365,
             nota="opcional"),
)

POR_API: dict[str, Pergunta] = {p.api: p for p in PERGUNTAS}

# Ordem canônica das colunas cruas. LightGBM casa feature por POSIÇÃO -- ordem
# trocada prevê em silêncio e errado (Arquitetura §3). Quem serializa grava
# esta ordem em `colunas.json`; quem serve a lê de lá e não daqui, porque o
# artefato pode ser mais velho que o código.
COLUNAS: tuple[str, ...] = tuple(c for p in PERGUNTAS for c in p.colunas)

CATEGORICAS: tuple[str, ...] = tuple(
    c for p in PERGUNTAS if p.tipo == "categorica" for c in p.colunas)
BOOLEANAS: tuple[str, ...] = tuple(
    c for p in PERGUNTAS if p.tipo == "booleana" for c in p.colunas)
NUMERICAS: tuple[str, ...] = tuple(
    c for p in PERGUNTAS if p.tipo in ("inteiro", "real") for c in p.colunas)

# Colunas que o usuário informa invertidas: ele responde "tem elevador", a
# coluna do treino diz "o anúncio declarou que NÃO tem".
INVERTIDAS: frozenset[str] = frozenset({"fam_sem_elevador"})


# --------------------------------------------------------------------------
# Vocabulário das categóricas
#
# Os níveis vêm do artefato treinado (`categorias.json`), não daqui -- é o
# treino que define o que o modelo conhece. O que este bloco define é o
# CONTRÁRIO: como a resposta do usuário vira um nível que existe.
# --------------------------------------------------------------------------

# `rx_vista` no treino tem 5 níveis, e dois deles não são resposta de usuário:
# `nao_menciona` (o anúncio não citou) e `sem_descricao` (não havia anúncio).
# Quem responde "nenhuma" cai em `nao_menciona`, que é o balde ambíguo -- é a
# distorção declarada em D-035, e ela aparece na resposta da API.
VISTA_USUARIO: dict[str, str] = {
    "mar_total": "mar_total",
    "mar_parcial": "mar_parcial",
    "livre": "livre",
    "nenhuma": "nao_menciona",
    "nao_sei": "nao_menciona",
}
VAGA_USUARIO: dict[str, str] = {
    "privativa_demarcada": "privativa_demarcada",
    "privativa": "privativa",
    "coletiva": "coletiva",
    "sorteio": "sorteio",
    "rotativa": "rotativa",
    "sem_vaga": "sem_vaga",
    "nao_sei": "nao_menciona",
}

# Níveis que existem no treino e NÃO são oferecidos ao usuário: são artefato de
# coleta, não atributo de imóvel.
NIVEIS_INTERNOS: frozenset[str] = frozenset({"sem_descricao", "nao_menciona"})


# Rótulo humano para os valores de vocabulário. `replace("_", " ")` devolvia
# "nao sei" e "privativa demarcada" na tela de um produto em português -- os
# valores internos são sem acento por serem chaves, e a chave não é o rótulo.
ROTULOS: dict[str, str] = {
    "nao_sei": "Não sei",
    "nao_menciona": "Não informado",
    "sem_descricao": "Sem descrição",
    "privativa_demarcada": "Privativa demarcada",
    "privativa": "Privativa",
    "coletiva": "Coletiva",
    "sorteio": "Por sorteio",
    "rotativa": "Rotativa",
    "sem_vaga": "Sem vaga",
    "mar_total": "Mar",
    "mar_parcial": "Mar parcial",
    "livre": "Livre",
    "nenhuma": "Nenhuma",
    "venda": "Comprar",
    "locacao": "Alugar",
    "apartamento": "Apartamento", "casa": "Casa", "sobrado": "Sobrado",
    "cobertura": "Cobertura", "kitnet": "Kitnet", "studio": "Studio",
    "flat": "Flat", "loja": "Loja", "sala": "Sala comercial",
    "galpao": "Galpão", "garagem": "Garagem", "terreno": "Terreno",
    "condominio": "Condomínio", "duplex": "Duplex",
    # Campos, para a tela poder citar "não informado: ar-condicionado".
    "ar_condicionado": "ar-condicionado", "area_m2": "área útil",
    "vaga_tipo": "tipo de vaga", "condominio_valor": "condomínio",
}


def rotulo(chave: str) -> str:
    """
    Texto de tela para um valor de vocabulário ou nome de campo.

    Sem tabela, cai para trocar `_` por espaço -- que é melhor que a chave crua
    e pior que uma entrada aqui. Vocabulário novo aparece legível e sem acento
    até alguém o traduzir, o que é visível na tela e não silencioso.
    """
    return ROTULOS.get(chave, chave.replace("_", " "))


def normaliza_tipo(valor) -> str | None:
    """
    Tipo do imóvel na forma canônica do `comum.vocab`.

    POR QUE ISTO EXISTE, e é uma mudança de instrumento declarada. O P03
    normaliza `city` e `neighborhood` e NÃO normaliza `property_type`: o
    recorte tem 78 níveis, com `apartamento` (3.212) e `Apartamento` (1.401)
    contados como categorias diferentes. Servir isso significaria um `select`
    com o mesmo tipo duas vezes, e a escolha do usuário caindo em metade da
    massa de treino.

    O efeito de normalizar é medido por `treina_final.py --ablacao`; não é
    assumido aqui.

    O QUE O VOCABULÁRIO NÃO RECONHECE VIRA NULO, e isso é decisão, não descuido.
    A primeira versão desta função caía para `vocab.dobra(valor)`, mantendo o
    texto cru como categoria. Medido no recorte: 28 linhas de 6.974 (0,40%) não
    são reconhecidas, e entre elas estão dois TÍTULOS DE ANÚNCIO que vazaram
    para o campo -- "o espaco ideal para o sucesso do seu negocio no canal 5" e
    "seu refugio na praia da enseada conforto". Com o fallback, esses títulos
    viravam níveis do modelo e apareciam no `select` do formulário via
    `GET /opcoes`. Nulo é o que eles são: defeito de extração, não tipo de
    imóvel, e o LightGBM trata nulo como nível próprio.

    LACUNA DE VOCABULÁRIO REGISTRADA, e ela não é consertada aqui: `Salas`
    (18 linhas) é o plural de `sala` e `vocab.normaliza_tipo` não o reconhece.
    O conserto pertence a `utils/vocab.py`, que a coleta inteira usa, e não a
    este módulo -- corrigir aqui deixaria coleta e calculadora com dois
    vocabulários.
    """
    if valor is None:
        return None
    if isinstance(valor, float) and np.isnan(valor):
        return None
    return vocab.normaliza_tipo(valor) or None


# --------------------------------------------------------------------------
# Montagem da base de treino
# --------------------------------------------------------------------------

class MontagemInvalida(Exception):
    """A base não pôde ser montada como o formulário exige. Nunca engolida."""


def _uma_linha_por_imovel(d: pd.DataFrame, nome: str) -> pd.DataFrame:
    if d.property_id.duplicated().any():
        n = int(d.property_id.duplicated().sum())
        raise MontagemInvalida(
            f"{nome}: {n} property_id repetidos -- o join escolheria uma linha "
            f"em silêncio")
    return d.set_index("property_id")


def familias(p02: pd.DataFrame) -> pd.DataFrame:
    """OR booleano dos membros de cada família. Indexado por `property_id`."""
    membros = sorted({m for ms in FAMILIAS.values() for m in ms})
    ausentes = [m for m in membros if m not in p02.columns]
    if ausentes:
        raise MontagemInvalida(
            f"P02 sem os membros de família {ausentes} -- a família sairia "
            f"menor do que a medida em MEDICOES §28")
    fam = _uma_linha_por_imovel(p02[["property_id"] + membros], "P02")
    saida = pd.DataFrame(index=fam.index)
    for nome, ms in FAMILIAS.items():
        saida[nome] = np.logical_or.reduce(
            [fam[m].fillna(False).astype(bool).values for m in ms])
    return saida


def monta_base(base: Path | str, data: str,
               normaliza_property_type: bool = True) -> pd.DataFrame:
    """
    P03 + `rx_*` do P01 + `fam_*` do P02, restrito ao formulário.

    As três camadas são necessárias e nenhuma delas basta:

      - P03 tem os físicos, a geografia e `amen_varanda` / `amen_piscina`,
        já filtrado por faixa de preço e de R$/m²;
      - `rx_vaga_tipo` e `rx_vista` NÃO existem em parquet -- são recalculadas
        de `description_clean` do P01 a cada montagem (o artefato em cache que
        existia estava corrompido, e foi isso que fez o plano registrar
        `rx_vaga_tipo` como quebrado; ver MEDICOES §27);
      - os membros raros das famílias só existem no P02: a poda por suporte
        do `construir_abt` descarta 246 das 438 colunas de amenidade.

    Levanta se o join perder linha. Join que perde linha em silêncio é a falha
    cara: o modelo treina com menos base e o RMSE só fica um pouco pior.
    """
    base = Path(base)
    d = dados.carrega(base, data)                    # P03 + holdout marcado

    p01 = pd.read_parquet(base / f"P01_{data}.parquet")
    faltam = [c for c in ("property_id", "description_clean")
              if c not in p01.columns]
    if faltam:
        raise MontagemInvalida(f"P01_{data} sem {faltam}")
    rx = regras_texto.aplica(p01[["property_id", "description_clean"]],
                             col="description_clean")
    rx = _uma_linha_por_imovel(rx, f"P01_{data}")[["rx_vaga_tipo", "rx_vista"]]

    fam = familias(pd.read_parquet(base / f"P02_{data}.parquet"))

    idx = pd.Index(d.property_id.values)
    for nome, tabela in (("rx (P01)", rx), ("fam (P02)", fam)):
        perdidas = int(idx.difference(tabela.index).size)
        if perdidas:
            raise MontagemInvalida(
                f"{perdidas} de {len(idx)} imóveis do P03 não existem em "
                f"'{nome}' -- datas de base diferentes?")

    d = d.reset_index(drop=True)
    for col in ("rx_vaga_tipo", "rx_vista"):
        d[col] = rx[col].reindex(idx).values
    for col in fam.columns:
        d[col] = fam[col].reindex(idx).values

    if normaliza_property_type:
        d["property_type"] = d.property_type.map(normaliza_tipo)

    for c in BOOLEANAS:
        d[c] = d[c].fillna(False).astype(bool)
    return d


def matriz(d: pd.DataFrame, colunas=None) -> pd.DataFrame:
    """
    O quadro que vai ao modelo, na ordem canônica.

    `colunas` vem de `colunas.json` na inferência: o artefato manda, não o
    código. Sem isso, acrescentar uma pergunta ao módulo quebraria em silêncio
    todo modelo já serializado.
    """
    cols = list(colunas) if colunas is not None else list(COLUNAS)
    faltam = [c for c in cols if c not in d.columns]
    if faltam:
        raise MontagemInvalida(f"faltam colunas na matriz: {faltam}")
    return d[cols]


# --------------------------------------------------------------------------
# Resposta do usuário -> linha
# --------------------------------------------------------------------------

def linha_do_usuario(resposta: dict) -> pd.DataFrame:
    """
    Converte um payload já validado num quadro de UMA linha, colunas cruas.

    Recebe as chaves da API (`api` de cada `Pergunta`), devolve as colunas do
    modelo. Não valida faixa nem enum -- isso é do contrato HTTP, que responde
    422; aqui o valor já chega bom.

    Ausente vira NaN (numérico), None (categórico) ou False (booleano). NaN em
    `condo_fee`/`iptu_tax` é exatamente o que `avaliacao.apaga_campos` produz no
    cenário de decisão, que é o regime em que o modelo foi medido.
    """
    linha: dict = {}
    for p in PERGUNTAS:
        v = resposta.get(p.api)
        if p.api == "vista":
            nivel = VISTA_USUARIO.get(v) if v is not None else None
            linha["rx_vista"] = nivel or "nao_menciona"
            continue
        if p.api == "vaga_tipo":
            nivel = VAGA_USUARIO.get(v) if v is not None else None
            linha["rx_vaga_tipo"] = nivel or "nao_menciona"
            continue
        if p.api == "tipo":
            linha["property_type"] = normaliza_tipo(v)
            continue
        for col in p.colunas:
            if p.tipo == "booleana":
                if col in INVERTIDAS:
                    # A coluna diz "o anúncio DECLAROU a ausência". Só o "não"
                    # explícito a liga. Não responder tem de cair no mesmo lado
                    # que "tem" -- é o lado de quem não declarou nada, que é
                    # onde vivem 97,92% das linhas de treino. Tratar ausência de
                    # resposta como `True` poria o imóvel no balde de 2,08% mais
                    # negativo da base sem que ninguém tenha dito nada.
                    linha[col] = v is False
                else:
                    linha[col] = bool(v) if v is not None else False
            elif p.tipo == "categorica":
                linha[col] = None if v is None else str(v)
            else:
                linha[col] = np.nan if v is None else float(v)
    quadro = pd.DataFrame([linha])
    for c in BOOLEANAS:
        quadro[c] = quadro[c].astype(bool)
    return quadro


def campos_ausentes(resposta: dict) -> list[str]:
    """Quais opcionais o usuário não informou. Vai na resposta da API."""
    return [p.api for p in PERGUNTAS
            if not p.obrigatoria and resposta.get(p.api) is None]


def ressalvas(resposta: dict) -> list[str]:
    """
    O que a resposta promete e a medição não sustenta, por pergunta respondida.

    Vem de D-035 e de MEDICOES §28. As colunas de amenidade nascem de texto de
    anúncio: o lado "sim" está alinhado (anúncio não menciona o que não existe),
    o lado "não" mistura "não tem" com "o anúncio não citou". Quem responde
    "não" recebe estimativa puxada na direção de um grupo misto.

    Isto não é decoração de UI. É a diferença entre informar e enganar, e por
    isso viaja na resposta em vez de ficar num rodapé de documentação.
    """
    fora: list[str] = []
    negadas = [p.rotulo for p in PERGUNTAS
               if p.tipo == "booleana" and resposta.get(p.api) is False]
    if negadas:
        fora.append(
            "As respostas 'não' às perguntas de comodidade são menos confiáveis "
            "que as 'sim': o modelo aprendeu com texto de anúncio, onde 'não "
            "mencionado' e 'não tem' são o mesmo valor. Afeta: "
            + "; ".join(negadas))
    if resposta.get("elevador") is False:
        fora.append(
            "A ausência de elevador foi aprendida de anúncios que a declararam "
            "(2,08% da base) -- um grupo mais estreito, e provavelmente mais "
            "barato, que o de prédios sem elevador. Diferença não medida.")
    if resposta.get("vista") in (None, "nenhuma", "nao_sei"):
        fora.append(
            "Sem vista informada, a estimativa usa o nível 'não mencionado', "
            "que mistura imóveis sem vista com anúncios omissos.")
    return fora
