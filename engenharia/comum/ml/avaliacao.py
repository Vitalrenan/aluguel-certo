"""
Protocolo de avaliação — §1 do plano-modelagem.md.

Existe porque a medição de 2026-08-29 mostrou que **um único split não resolve
nada nesta base**: o erro mediano da venda ≤800k saiu 16,1% no teste e 12,9% na
validação, e o bootstrap mostrou intervalos de 3,3 e 4,7 pontos de largura, que
se sobrepõem. Eram o mesmo número visto com ruído de amostragem.

Consequência: qualquer ganho menor que ~3 pontos é indetectável num split só, por
mais sementes que se rode — semente não é a fonte dominante de incerteza, o
tamanho do conjunto de avaliação é. A saída é rodízio: cada linha serve de teste
uma vez por repetição, e o erro é medido sobre a base inteira.

Três invariantes que este módulo garante:

1. AGRUPAMENTO. 12,8% das linhas são o mesmo imóvel anunciado duas vezes. Cópias
   nunca cruzam a fronteira de fold.
2. FOLDS FIXOS. As atribuições são determinísticas dada a semente, então duas
   configurações quaisquer são comparadas nos MESMOS folds. Sem isso a comparação
   pareada do §1.4 não existe.
3. CENÁRIO DE PREDIÇÃO. `iptu_tax` e `condo_fee` são opcionais para o usuário
   (§3.5). A métrica de decisão é medida com os dois ausentes -- o caso comum --
   e não com a base cheia, que tem 85,8% e 77,6% de preenchimento.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

# Campos que o usuário pode não informar. A métrica de manchete os apaga.
OPCIONAIS = ("iptu_tax", "condo_fee")

CENARIOS = {
    "sem_opcionais": OPCIONAIS,       # decisão -- o usuário típico
    "so_condominio": ("iptu_tax",),
    "completo": (),
}
CENARIO_DECISAO = "sem_opcionais"

# Métrica que DECIDE promoção. Trocada de "mae_log" para "rmse_brl" em
# 2026-08-30. Tudo o mais é leitura de acompanhamento.
METRICA_DECISAO = "rmse_brl"


def _dobra(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def chave_de_grupo(d: pd.DataFrame) -> pd.Series:
    """
    O mesmo imóvel anunciado duas vezes cai no mesmo grupo.

    Medido: 280 grupos duplicados, 171 dentro da mesma agência e 109 entre
    agências diferentes. Split por linha põe cópias dos dois lados e mede
    memorização como se fosse generalização.

    MUDANÇA DE 2026-08-30 -- QUEBRA COMPARABILIDADE COM O LEADERBOARD ANTERIOR.
    A versão antiga incluía `price` na chave, então só agrupava a duplicata de
    preço IDÊNTICO -- o caso fácil. Os 437 anúncios do mesmo imóvel com preços
    diferentes caíam em folds diferentes, e o mesmo imóvel aparecia no treino e
    no teste. Agora a chave é a de identidade (`ml.dedup.CHAVE`, sem preço).

    Linha com nulo na chave recebe grupo próprio: não se afirma que dois imóveis
    são o mesmo com base num campo que não se conhece. Ver `tests/test_dedup.py`.

    Toda medição anterior a esta data usou a chave antiga. Números novos e
    antigos NÃO são comparáveis; refaça a medição antes de comparar.
    """
    from comum.ml import dedup

    k = dedup.chave(d)
    # Inelegível vira grupo unitário, nunca um balde comum.
    solto = pd.Series([f"__so_{i}__" for i in range(len(d))], index=d.index)
    return k.fillna(solto).astype(str)


def folds(d: pd.DataFrame, n_folds: int = 5, n_repeticoes: int = 5,
          semente: int = 20260829) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    GroupKFold repetido, estratificado por decil de preço.

    Estratificar importa: sem isso um fold pode receber cauda demais e virar
    outlier de score. Agrupar importa mais: sem isso o score é fantasia.

    Devolve [(idx_treino, idx_teste)] com n_folds * n_repeticoes elementos, na
    ordem -- a posição é a identidade do fold, e é o que permite parear duas
    configurações.
    """
    grupos = chave_de_grupo(d).values
    unicos, inverso = np.unique(grupos, return_inverse=True)
    # Um grupo herda o decil do seu preço mediano: o estrato é do grupo, não da
    # linha, senão o mesmo grupo tentaria entrar em dois estratos.
    preco_grupo = pd.Series(d.price.values).groupby(inverso).median()
    decil = pd.qcut(preco_grupo.rank(method="first"), 10, labels=False).values

    saida = []
    for r in range(n_repeticoes):
        rng = np.random.default_rng(semente + r)
        atribuicao = np.empty(len(unicos), dtype=int)
        for estrato in np.unique(decil):
            alvo = np.flatnonzero(decil == estrato)
            baralho = rng.permutation(alvo)
            atribuicao[baralho] = np.arange(len(baralho)) % n_folds
        fold_da_linha = atribuicao[inverso]
        for k in range(n_folds):
            teste = np.flatnonzero(fold_da_linha == k)
            treino = np.flatnonzero(fold_da_linha != k)
            saida.append((treino, teste))
    return saida


def apaga_campos(X: pd.DataFrame, campos, linhas=None) -> pd.DataFrame:
    """
    Apaga campos **e tudo que deles deriva**, nas linhas indicadas.

    `linhas=None` apaga a coluna inteira (cenário de predição); uma máscara
    booleana apaga só as linhas marcadas (dropout de treino). É a mesma
    operação nos dois casos, e por isso mora num lugar só.

    Apagar um campo significa três coisas, não uma:

      1. o valor sai;
      2. as derivadas de `dados.DERIVADAS` saem -- `condo_por_m2 * area_m2`
         devolve `condo_fee`, e `area_m2` continua presente;
      3. a bandeira `aus_<campo>` passa a `True` e `n_campos_ausentes` é
         recontado -- é o que o modelo vê quando o dado de fato falta.

    Fazer só (1) esconde o campo da leitura humana e não do modelo.
    """
    from comum.ml import dados

    Y = X.copy()
    tocou = False
    todas = linhas is None
    for campo in campos:
        if campo not in Y.columns:
            continue
        alvos = [campo] + [d for d in dados.DERIVADAS.get(campo, ())
                           if d in Y.columns]
        for col in alvos:
            if todas:
                Y[col] = np.nan
            else:
                Y.loc[linhas, col] = np.nan
        bandeira = f"aus_{campo}"
        if bandeira in Y.columns:
            if todas:
                Y[bandeira] = True
            else:
                Y.loc[linhas, bandeira] = True
        tocou = True

    if tocou and "n_campos_ausentes" in Y.columns:
        flags = [c for c in Y.columns if c.startswith("aus_")]
        if flags:
            Y["n_campos_ausentes"] = Y[flags].sum(axis=1)
    return Y


def aplica_cenario(X: pd.DataFrame, cenario: str) -> pd.DataFrame:
    """
    Apaga os campos que o usuário não informaria neste cenário.

    DEFEITO CORRIGIDO EM 2026-09-07 -- QUEBRA COMPARABILIDADE COM TODO NÚMERO
    `sem_opcionais` ANTERIOR. A versão antiga apagava `condo_fee` e `iptu_tax`
    e deixava `condo_por_m2` / `iptu_por_m2` intactas. Como `area_m2` continua
    presente na predição, o valor apagado voltava por multiplicação: o cenário
    de decisão media o modelo enxergando exatamente o dado que o cenário existe
    para esconder. As bandeiras `aus_*` seguiam dizendo "informado", que é a
    segunda metade da mesma mentira.

    Medido em 2026-09-07, Santos, venda ≤800k, 25 folds pareados, lgbm padrão:

        como estava                          RMSE 101.458
        + apagando as derivadas              RMSE 104.214   +2.756, 25/25 folds
        + apagando também as bandeiras       RMSE 106.056   +4.598, 25/25, p=2,1e-13

    Todo `sem_opcionais` registrado antes desta data está otimista em ~4,6 mil
    e NÃO é comparável com os posteriores. Refaça a medição antes de comparar.
    """
    return apaga_campos(X, CENARIOS[cenario])


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------

def metricas(y_log: np.ndarray, p_log: np.ndarray, preco: np.ndarray) -> dict:
    """
    RMSE em R$ e as leituras de acompanhamento.

    **RMSE em R$ decide.** Decisão de 2026-08-30, sobrepondo a proposta original
    de MAE-log. A objeção que eu fazia -- que a cauda domina o RMSE -- perdeu
    força com o corte em 800k, que já removeu os extremos: no universo ≤800k o
    RMSE tem desvio entre folds de ~5k sobre ~105k, que é comportado.

    Registrado porque a troca INVERTE uma conclusão: o CatBoost vence por
    MAE-log (0,1899, melhor erro mediano) e perde por RMSE R$ (122.086, o pior
    dos três baselines). Ele acerta mais no caso típico e erra mais feio nos
    casos ruins; o quadrado do erro pune isso e a média do log não.
    """
    e_log = p_log - y_log
    rel = np.abs(np.expm1(e_log))
    assinado = np.expm1(e_log)          # >0 superestima, <0 subestima
    prev = np.exp(p_log)
    return {
        "mae_log": float(np.mean(np.abs(e_log))),
        "rmse_log": float(np.sqrt(np.mean(e_log ** 2))),
        "erro_mediano": float(np.median(rel)),
        "p90_erro": float(np.quantile(rel, 0.90)),
        "rmse_brl": float(np.sqrt(np.mean((prev - preco) ** 2))),
        "mae_brl": float(np.mean(np.abs(prev - preco))),
        "dentro_10pct": float(np.mean(rel <= 0.10)),
        "dentro_20pct": float(np.mean(rel <= 0.20)),
        # --- VIÉS: as únicas métricas COM SINAL deste módulo ---------------
        #
        # Acrescentadas em 2026-09-09. Até aqui TODAS as leituras passavam por
        # `np.abs` ou por um quadrado, e nenhuma delas distingue um modelo que
        # erra 12% para cima de um que erra 12% para baixo. São erros
        # diferentes para o produto: superestimar sistematicamente faz o
        # vendedor anunciar caro e o imóvel encalhar; subestimar faz vender
        # barato. O RMSE dá o mesmo número nos dois casos.
        #
        # Três recortes porque respondem a perguntas diferentes:
        #
        #   vies_brl      média de (previsto - real), em R$. É o desvio da
        #                 CARTEIRA -- avaliar 1.000 imóveis erra tanto para
        #                 cima no total. Dominado pelos caros.
        #   vies_pct      média de (previsto/real - 1). Dá o mesmo peso ao
        #                 imóvel de 200 mil e ao de 800 mil. É assimétrico por
        #                 construção: errar 2x para cima é +100%, 2x para
        #                 baixo é -50%, então ele puxa para o positivo.
        #   vies_mediano  mediana de (previsto/real - 1). É o do caso TÍPICO,
        #                 imune à cauda e à assimetria acima. Quando os três
        #                 discordam, este é o que descreve o usuário comum.
        #
        # Viés perto de zero NÃO significa modelo calibrado: a compressão
        # medida neste projeto superestima o decil barato e subestima o caro,
        # e as duas metades se cancelam na média. Ler junto com a calibração
        # por decil de `treina_final.calibracao`.
        "vies_brl": float(np.mean(prev - preco)),
        "vies_pct": float(np.mean(assinado)),
        "vies_mediano": float(np.median(assinado)),
        "vies_log": float(np.mean(e_log)),
    }


@dataclass
class Resultado:
    nome: str
    segmento: str
    cenario: str
    por_fold: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def n_folds(self) -> int:
        return len(self.por_fold)

    def serie(self, metrica: str) -> np.ndarray:
        return np.array([f[metrica] for f in self.por_fold])

    def media(self, metrica: str = METRICA_DECISAO) -> float:
        return float(self.serie(metrica).mean())

    def desvio(self, metrica: str = METRICA_DECISAO) -> float:
        return float(self.serie(metrica).std(ddof=1))

    def erro_padrao(self, metrica: str = METRICA_DECISAO) -> float:
        return self.desvio(metrica) / np.sqrt(self.n_folds)

    def resumo(self) -> dict:
        d = {"nome": self.nome, "segmento": self.segmento, "cenario": self.cenario,
             "n_folds": self.n_folds}
        for m in ("rmse_brl", "mae_brl", "erro_mediano", "mae_log", "dentro_20pct",
                  "vies_brl", "vies_mediano"):
            d[m] = self.media(m)
        d["rmse_brl_sd"] = self.desvio()
        return d


def compara(campeao: Resultado, desafiante: Resultado,
            metrica: str = METRICA_DECISAO) -> dict:
    """
    Comparação pareada nos mesmos folds -- §1.4.

    Wilcoxon pareado, não teste t: com 25 folds a normalidade não está dada e o
    pareamento é o que remove a variância de fold, que domina a de modelo.

    Devolve o p bruto; a correção de Benjamini-Hochberg é aplicada sobre a
    rodada inteira em `benjamini_hochberg`, porque corrigir um teste isolado não
    significa nada.
    """
    if campeao.n_folds != desafiante.n_folds:
        raise ValueError("folds diferentes -- comparação pareada impossível")
    a, b = campeao.serie(metrica), desafiante.serie(metrica)
    dif = b - a
    if np.allclose(dif, 0):
        p = 1.0
    else:
        p = float(stats.wilcoxon(a, b, zero_method="zsplit").pvalue)
    return {
        "campeao": campeao.nome, "desafiante": desafiante.nome,
        "media_campeao": float(a.mean()), "media_desafiante": float(b.mean()),
        "delta": float(dif.mean()),
        "delta_pct": float(dif.mean() / a.mean()),
        "venceu_em": int((dif < 0).sum()), "de": len(dif),
        "p": p,
    }


def benjamini_hochberg(ps: list[float], alfa: float = 0.01) -> list[bool]:
    """
    Quais p sobrevivem à correção BH. Um AutoML roda milhares de comparações; a
    5% um em vinte "vence" por acaso, e sem isto o leaderboard enche de ruído.
    """
    n = len(ps)
    if n == 0:
        return []
    ordem = np.argsort(ps)
    aprovado = np.zeros(n, dtype=bool)
    maior = -1
    for posicao, i in enumerate(ordem, start=1):
        if ps[i] <= alfa * posicao / n:
            maior = posicao
    if maior > 0:
        aprovado[ordem[:maior]] = True
    return aprovado.tolist()


def promove(comparacao: dict, aprovado_bh: bool, alfa: float = 0.01) -> bool:
    """Regra do §1.4: melhor em média E significativo E sobrevivendo ao BH."""
    return (comparacao["delta"] < 0 and comparacao["p"] < alfa and aprovado_bh)


def bootstrap_ic(valores: np.ndarray, n: int = 4000, semente: int = 0
                 ) -> tuple[float, float]:
    rng = np.random.default_rng(semente)
    bs = [np.median(rng.choice(valores, size=len(valores), replace=True))
          for _ in range(n)]
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(lo), float(hi)
