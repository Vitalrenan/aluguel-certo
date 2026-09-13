"""
Executor de experimentos — §6 do plano.

Roda uma configuração sob o protocolo de `avaliacao.py` e devolve um `Resultado`
com o score de cada fold. Toda comparação posterior é pareada sobre esses folds.

O que este módulo garante e o resto do código não precisa lembrar:

  - o holdout NUNCA entra em nenhum fold; ele só é tocado por `avalia_holdout`,
    chamado uma vez, no fim, pelo campeão;
  - o target encoding é ajustado dentro de cada fold de treino;
  - a máscara de cenário (§3.5) é aplicada só na PREDIÇÃO, e ANTES da
    imputação -- o treino vê o dado completo a menos que `dropout` peça o
    contrário, e as duas máscaras apagam o campo com suas derivadas e
    bandeiras (`avaliacao.apaga_campos`).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import avaliacao, dados, dedup, geo
from .modelos import REGISTRO, Modelo


@dataclass
class Config:
    nome: str
    modelo: str = "lgbm"
    params: dict = field(default_factory=dict)
    n_sementes: int = 1
    segmento: str = "venda_ate800k"
    target_encoding: bool = False
    features_basicas: bool = True
    dropout_opcionais: float = 0.0
    # Estima `iptu_tax` e `condo_fee` quando faltam, com modelo ajustado DENTRO
    # do fold de treino. Medido em 2026-08-31: -12.047 R$ no cenario de decisao
    # (25/25 folds) e +52 R$ quando o usuario informa os dois. Ver
    # `dados.Imputador`.
    imputa_opcionais: bool = False
    # Features espaciais (§3): eixos rotacionados, interacoes dos eixos e o
    # preco mediano dos K vizinhos em coordenada. Medido em 2026-09-05:
    # -2.109 R$ em Sao Paulo (21/25 folds) e -314 em Santos. A diferenca e o
    # resultado -- em Santos o bairro ja codifica a geografia.
    espaciais: bool = False
    cenario: str = avaliacao.CENARIO_DECISAO

    def chave(self) -> str:
        d = {k: v for k, v in self.__dict__.items() if k != "nome"}
        return json.dumps(d, sort_keys=True, default=str)


def _constroi(cfg: Config) -> Modelo:
    return REGISTRO[cfg.modelo](params=cfg.params, n_sementes=cfg.n_sementes)


def _mascara_treino(X: pd.DataFrame, fracao: float, semente: int) -> pd.DataFrame:
    """
    Feature dropout: ensina o modelo a operar sem os campos opcionais.

    Usa `avaliacao.apaga_campos` -- a MESMA operação do cenário de predição.
    Até 2026-09-07 esta função apagava só `condo_fee` e `iptu_tax` e deixava
    `condo_por_m2` / `iptu_por_m2` e as bandeiras `aus_*` intactas: a linha
    "mascarada" continuava carregando o valor, e o dropout não ensinava nada.
    Toda varredura de `dropout_opcionais` anterior a esta data mediu ruído.
    """
    if fracao <= 0:
        return X
    rng = np.random.default_rng(semente)
    linhas = rng.random(len(X)) < fracao
    return avaliacao.apaga_campos(X, avaliacao.OPCIONAIS, linhas=linhas)


def roda(cfg: Config, base: pd.DataFrame, n_folds: int = 5,
         n_repeticoes: int = 5, verboso: bool = False,
         particoes: list | None = None) -> avaliacao.Resultado:
    """
    `particoes` existe para o caso em que os bracos MUDAM a chave de grupo.

    `chave_de_grupo` usa `property_type`; normalizar essa coluna funde
    `apartamento` e `Apartamento` no mesmo grupo e o fold sai diferente. Dois
    bracos com folds diferentes nao sao comparaveis pareados -- e o teste de
    Wilcoxon do §1.4 nem existiria. Quem compara instrumento que mexe na chave
    calcula as particoes uma vez, de um quadro de referencia, e passa aqui.
    """
    d = dados.segmenta(base, cfg.segmento)
    d = d[~d._holdout].reset_index(drop=True)     # holdout fica de fora, sempre
    if cfg.features_basicas:
        d = dados.basicas(d)

    if particoes is None:
        particoes = avaliacao.folds(d, n_folds, n_repeticoes)
    elif max(max(tr.max(), te.max()) for tr, te in particoes) >= len(d):
        raise ValueError("particoes de outro recorte -- indices fora do quadro")
    res = avaliacao.Resultado(cfg.nome, cfg.segmento, cfg.cenario)
    t0 = time.time()

    for i, (i_tr, i_te) in enumerate(particoes):
        tr, te = d.iloc[i_tr].copy(), d.iloc[i_te].copy()
        # ALVO deduplicado, TREINO cru -- e uma divisao, nao um sim ou nao.
        # Medido em 2026-08-30 (15 folds): deduplicar o alvo tira 9.462 R$ do
        # RMSE porque para de cobrar do modelo o desacordo entre dois anuncios
        # do mesmo imovel; deduplicar o TREINO custa +10.511 R$ e perde 15/15,
        # porque as copias sao sorteios independentes e o modelo ja faz a media.
        te = dedup.funde(te).reset_index(drop=True)
        # CENARIO ANTES DA IMPUTACAO (2026-09-07). Aplicado depois, ele apagava
        # o que o imputador acabara de estimar e `imputa_opcionais` virava
        # no-op silencioso no cenario de decisao -- config ligada, efeito zero.
        # Em producao a ordem e esta: o usuario nao informa, e so entao o
        # servico estima. Sem `imputa_opcionais` as duas ordens sao iguais.
        te = avaliacao.aplica_cenario(te, cfg.cenario)
        # Antes do target encoding: a imputacao mexe em `condo_por_m2` e
        # `iptu_por_m2`, e o encoder nao depende delas.
        if cfg.imputa_opcionais:
            imp = dados.Imputador().ajusta(tr)
            tr, te = imp.aplica(tr), imp.aplica(te)
        if cfg.espaciais:
            tr, te = geo.interacoes(tr), geo.interacoes(te)
            rot = geo.Rotacao().ajusta(tr)
            tr, te = rot.aplica(tr), rot.aplica(te)
            viz = geo.VizinhancaAlvo().ajusta(tr)
            # `treino=True` exclui a propria linha da sua vizinhanca.
            tr, te = viz.aplica(tr, treino=True), viz.aplica(te)
        if cfg.target_encoding:
            enc = dados.Encoder().ajusta(tr)
            tr, te = enc.aplica(tr), enc.aplica(te)

        # Fatia interna de parada, tirada do TREINO. Usar o fold de teste aqui
        # escolheria o nº de árvores olhando o que se pretende medir.
        corte = int(len(tr) * 0.85)
        emb = np.random.default_rng(i).permutation(len(tr))
        aj, pa = tr.iloc[emb[:corte]], tr.iloc[emb[corte:]]

        X_aj = _mascara_treino(dados.matriz(aj), cfg.dropout_opcionais, i)
        X_pa = _mascara_treino(dados.matriz(pa), cfg.dropout_opcionais, i + 1)
        m = _constroi(cfg).ajusta(X_aj, aj.log_price.values,
                                  X_pa, pa.log_price.values)

        X_te = dados.matriz(te)
        p = m.preve(X_te)
        res.por_fold.append(avaliacao.metricas(te.log_price.values, p, te.price.values))
        if verboso and (i + 1) % 5 == 0:
            print(f"    fold {i+1}/{len(particoes)}  "
                  f"mae_log parcial {res.media():.4f}", flush=True)

    res.extra = {"segundos": round(time.time() - t0, 1), "n_linhas": len(d),
                 "chave": cfg.chave()}
    return res


def avalia_holdout(cfg: Config, base: pd.DataFrame) -> dict:
    """
    Chamado UMA vez, pelo campeão. Treina em tudo que não é holdout e mede no
    holdout. Se isto for chamado durante a busca, o holdout deixa de existir.
    """
    d = dados.segmenta(base, cfg.segmento)
    if cfg.features_basicas:
        d = dados.basicas(d)
    tr, ho = d[~d._holdout].copy(), dedup.funde(d[d._holdout]).reset_index(drop=True)
    ho = avaliacao.aplica_cenario(ho, cfg.cenario)     # antes da imputacao
    if cfg.imputa_opcionais:
        imp = dados.Imputador().ajusta(tr)
        tr, ho = imp.aplica(tr), imp.aplica(ho)
    if cfg.espaciais:
        tr, ho = geo.interacoes(tr), geo.interacoes(ho)
        rot = geo.Rotacao().ajusta(tr)
        tr, ho = rot.aplica(tr), rot.aplica(ho)
        viz = geo.VizinhancaAlvo().ajusta(tr)
        tr, ho = viz.aplica(tr, treino=True), viz.aplica(ho)
    if cfg.target_encoding:
        enc = dados.Encoder().ajusta(tr)
        tr, ho = enc.aplica(tr), enc.aplica(ho)
    corte = int(len(tr) * 0.85)
    emb = np.random.default_rng(0).permutation(len(tr))
    aj, pa = tr.iloc[emb[:corte]], tr.iloc[emb[corte:]]
    m = _constroi(cfg).ajusta(
        _mascara_treino(dados.matriz(aj), cfg.dropout_opcionais, 0),
        aj.log_price.values,
        _mascara_treino(dados.matriz(pa), cfg.dropout_opcionais, 1),
        pa.log_price.values)
    X = dados.matriz(ho)
    return avaliacao.metricas(ho.log_price.values, m.preve(X), ho.price.values)


class Registro:
    """Leaderboard append-only. Uma linha por execução, nunca sobrescrita."""

    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.linhas: list[dict] = []

    def grava(self, res: avaliacao.Resultado):
        linha = res.resumo() | {"segundos": res.extra.get("segundos"),
                                "n_linhas": res.extra.get("n_linhas")}
        self.linhas.append(linha)
        with self.caminho.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")

    def quadro(self) -> pd.DataFrame:
        return pd.DataFrame(self.linhas).sort_values("mae_log")
