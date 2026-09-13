"""
Contrato HTTP — Arquitetura §5 e §6.

DUAS REGRAS DE COMPORTAMENTO, e as duas são deliberadas.

**Fora de faixa é 422, não anulação.** Na coleta, anular o valor implausível é
certo -- a alternativa é perder a linha inteira, e ninguém pode reabrir a
página. Na calculadora o usuário está diante da tela: `area_m2 = 8000` é dedo
escorregado, e devolver uma estimativa em cima disso é pior do que devolver
erro.

**As faixas vêm de `utils.schema`, não de uma cópia em Pydantic.** Duas tabelas
de faixa divergem no primeiro ajuste, e a divergência não levanta exceção
nenhuma -- o serviço passa a aceitar o que a coleta rejeita. Por isso os
limites são lidos de `formulario.PERGUNTAS`, que por sua vez os lê de
`schema.FAIXAS` e `schema.FAIXAS_FISICAS`.

**Não há campo de pessoa neste contrato, e não deve haver** (Arquitetura §9).
Nome, telefone, e-mail e CPF não entram; a defesa é o formulário fechado, não
um scrubber. O serviço também não persiste requisição: um endereço aproximado
mais uma faixa de preço identifica um imóvel, e um imóvel tem dono.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Nada de acrobacia de sys.path: `engenharia` e pacote instalavel e o import
# e declarado. O `serving/` antigo alcancava o pipeline apontando sys.path a
# mao, o que quebrava ao mover qualquer das duas pastas -- e quebrava em
# silencio se houvesse um modulo de mesmo nome antes no caminho.

from comum.ml import formulario  # noqa: E402

_FX = {p.api: p.faixa for p in formulario.PERGUNTAS if p.faixa}

Vista = Literal["mar_total", "mar_parcial", "livre", "nenhuma", "nao_sei"]
VagaTipo = Literal["privativa_demarcada", "privativa", "coletiva", "sorteio",
                   "rotativa", "sem_vaga", "nao_sei"]


class Estimativa(BaseModel):
    """
    O formulário medido: 18 perguntas de atributo, mais transação, mais dois
    opcionais. Os nomes são os de `formulario.PERGUNTAS[].api`.

    `extra="forbid"` de propósito: um cliente que mande `dormitorios_` por erro
    de digitação recebe 422 em vez de uma estimativa calculada sem dormitórios.
    Aceitar campo desconhecido em silêncio é a mesma falha do `--data` que o
    `construir_abt` ignorava calado.
    """
    model_config = ConfigDict(extra="forbid")

    transacao: Literal["venda", "locacao"] = "venda"
    cidade: str = Field(min_length=1, max_length=80)
    bairro: str = Field(min_length=1, max_length=80)
    tipo: str = Field(min_length=1, max_length=60)

    area_m2: float
    dormitorios: int
    banheiros: int
    suites: int
    vagas: int
    vaga_tipo: VagaTipo = "nao_sei"
    andar: int | None = None

    # Tri-estado de propósito: `None` é "não respondeu", `False` é "não tem".
    # Para o modelo os dois viram a mesma coluna `False` -- no treino, "o
    # anúncio não mencionou" e "não tem" já são o mesmo valor, e inventar um
    # terceiro nível criaria uma categoria que o modelo nunca viu. A diferença
    # existe para a RESSALVA: avisar sobre um "não" que o usuário deu é
    # informação; avisar sobre um "não" que o cliente HTTP deixou de mandar é
    # ruído que ensina o usuário a ignorar o aviso.
    varanda: bool | None = None
    vista: Vista = "nao_sei"
    piscina: bool | None = None
    elevador: bool | None = None
    sauna: bool | None = None
    academia: bool | None = None
    quintal: bool | None = None
    ar_condicionado: bool | None = None

    condominio: float | None = None
    iptu: float | None = None

    @field_validator("area_m2", "dormitorios", "banheiros", "suites", "vagas",
                     "andar", "condominio", "iptu")
    @classmethod
    def _dentro_da_faixa(cls, v, info):
        if v is None:
            return v
        lo, hi = _FX[info.field_name]
        if not (lo <= float(v) <= hi):
            raise ValueError(
                f"fora da faixa plausível do contrato de dados: {lo:g} a {hi:g}")
        return v

    @field_validator("suites")
    @classmethod
    def _suites_nao_passam_de_dormitorios(cls, v, info):
        # CONTRATO-DE-DADOS §6 registra `suites > bedrooms` como contradição
        # observada em 20 linhas, com a ressalva de que pode ser convenção da
        # fonte. Na coleta isso é dúvida; num formulário respondido por quem
        # mora no imóvel, é erro de digitação.
        q = info.data.get("dormitorios")
        if q is not None and v > q:
            raise ValueError(f"suítes ({v}) não podem passar de dormitórios ({q})")
        return v

    def para_modelo(self) -> dict:
        return self.model_dump()


class Intervalo(BaseModel):
    minimo: float = Field(alias="min")
    maximo: float = Field(alias="max")
    model_config = ConfigDict(populate_by_name=True)


class Modelo(BaseModel):
    versao: str
    base: str
    segmento: str
    n_treino: int
    treinado_em: str
    rmse_holdout: float | None = None
    mae_holdout: float | None = None
    erro_mediano_holdout: float | None = None
    dentro_20pct_holdout: float | None = None
    cobertura_intervalo: float | None = None
    # Viés COM SINAL: positivo é superestimar. Vai junto porque as outras
    # quatro leituras são todas absolutas, e uma tela que mostra só magnitude
    # não distingue um modelo que puxa o preço para cima de um que puxa para
    # baixo -- e essa é a diferença entre anunciar caro e vender barato.
    vies_brl_holdout: float | None = None
    vies_mediano_holdout: float | None = None


class Yield(BaseModel):
    """Procedência da taxa. Vai inteira para a tela poder citar a fonte."""
    cidade: str
    mensal: float
    mensal_p10: float
    mensal_p90: float
    fonte: str
    referencia: str
    medido_em: str
    n: int | None = None
    ressalvas: list[str] = []


class Aluguel(BaseModel):
    """
    Aluguel derivado do preço, pelo yield da cidade.

    `tipo` sai na resposta de propósito, e vale "ancora_de_mercado": é o que
    impede o front de apresentar uma multiplicação por taxa de referência como
    resultado do modelo. O modelo prevê PREÇO DE VENDA; o aluguel é uma
    premissa de negócio aplicada sobre esse preço.

    `envelope` não se chama `intervalo` porque não é a mesma coisa que a faixa
    do preço: aquela tem cobertura medida no holdout, esta é o pior caso de
    duas incertezas multiplicadas e não tem cobertura medida.
    """
    estimativa: float
    envelope: Intervalo
    envelope_cobertura_medida: float | None = None
    largura_do_envelope: float
    tipo: str
    yield_: Yield = Field(alias="yield")
    nota: str
    model_config = ConfigDict(populate_by_name=True)


class Resposta(BaseModel):
    """
    O ponto NUNCA viaja sozinho.

    Arquitetura §5: devolver `estimativa` só comunica uma precisão que a medição
    não sustenta. O intervalo não é enfeite de UI -- é a informação honesta, e é
    a diferença entre um produto que informa e um que engana. Por isso
    `intervalo` é obrigatório no esquema, e não opcional.
    """
    estimativa: float
    intervalo: Intervalo
    # `None` quando a cidade não tem yield com fonte declarada. Nunca uma taxa
    # padrão: um aluguel inventado sai com a mesma cara de um derivado de
    # medição, e o usuário não tem como distinguir os dois.
    aluguel: Aluguel | None = None
    modelo: Modelo
    campos_ausentes: list[str] = []
    ressalvas: list[str] = []


class Opcao(BaseModel):
    valor: str
    rotulo: str


class Pergunta(BaseModel):
    api: str
    rotulo: str
    tipo: str
    obrigatoria: bool
    faixa: list[float] | None = None
    opcoes: list[Opcao] | None = None
    delta_rmse: int | None = None
    nota: str = ""
