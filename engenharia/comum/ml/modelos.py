"""
Envelopes de LightGBM, XGBoost e CatBoost com interface única.

Os três recebem o mesmo quadro e devolvem previsão em log(preço). A diferença de
tratamento de categórica é o ponto: é onde os três discordam, e é o motivo de
CatBoost estar aqui apesar de não ter sido pedido -- ele faz *ordered target
statistics*, que é target encoding com proteção nativa contra vazamento, e o
problema tem bairro com 183 níveis e CEP com 624.

`n_sementes` faz média de previsões sobre sementes diferentes. Medido em
2026-08-29: o desvio entre sementes é de ±900 R$ no RMSE da venda ≤800k, maior
que quase todo efeito testado. Promediar remove essa variância sem risco de
overfit -- §5.1 do plano, o ganho mais barato disponível.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# `rx_vaga_tipo` e `rx_vista` sao categoricas de texto produzidas por
# `regras_texto.aplica`, nao ordinais -- os `_ord` correspondentes sao outras
# colunas. Sem estarem aqui elas chegam ao LightGBM como `object` e ele levanta;
# pior, um pipeline que as descartasse silenciosamente perderia duas das 13
# perguntas do formulario (D-035) sem aviso nenhum.
CATEGORICAS = ["transaction_type", "property_type", "city", "neighborhood",
               "cep_prefix", "familia", "rx_vaga_tipo", "rx_vista"]


def colunas_categoricas(X: pd.DataFrame) -> list[str]:
    return [c for c in CATEGORICAS if c in X.columns]


@dataclass
class Modelo:
    """Base. Subclasse implementa `_ajusta_um` e `_preve_um`."""
    params: dict = field(default_factory=dict)
    n_sementes: int = 1
    n_arvores: int = 3000
    paciencia: int = 100
    _ajustados: list = field(default_factory=list, init=False)
    _cats: dict = field(default_factory=dict, init=False)

    nome_base = "modelo"

    @property
    def nome(self) -> str:
        s = f"x{self.n_sementes}" if self.n_sementes > 1 else ""
        return f"{self.nome_base}{s}"

    def _niveis(self, X: pd.DataFrame) -> dict:
        return {c: pd.CategoricalDtype(sorted(X[c].dropna().astype(str).unique()))
                for c in colunas_categoricas(X)}

    def _prepara(self, X: pd.DataFrame, treino: bool) -> pd.DataFrame:
        if treino:
            self._cats = self._niveis(X)
        Y = X.copy()
        for c, dtype in self._cats.items():
            if c in Y.columns:
                Y[c] = Y[c].astype(str).where(Y[c].notna()).astype(dtype)
        for c in Y.columns:
            if Y[c].dtype == bool:
                Y[c] = Y[c].astype(np.int8)
        return Y

    def ajusta(self, X: pd.DataFrame, y: np.ndarray,
               X_val: pd.DataFrame | None = None, y_val: np.ndarray | None = None):
        Xt = self._prepara(X, treino=True)
        Xv = self._prepara(X_val, treino=False) if X_val is not None else None
        self._ajustados = [self._ajusta_um(Xt, y, Xv, y_val, semente=1000 + 7 * i)
                           for i in range(self.n_sementes)]
        return self

    def preve(self, X: pd.DataFrame) -> np.ndarray:
        Xp = self._prepara(X, treino=False)
        return np.mean([self._preve_um(m, Xp) for m in self._ajustados], axis=0)

    # -- a implementar -----------------------------------------------------
    def _ajusta_um(self, X, y, X_val, y_val, semente):
        raise NotImplementedError

    def _preve_um(self, ajustado, X):
        raise NotImplementedError


class LGBM(Modelo):
    nome_base = "lgbm"
    PADRAO = dict(objective="regression", metric="l1", learning_rate=0.03,
                  num_leaves=15, min_data_in_leaf=40, feature_fraction=0.6,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
                  verbosity=-1)

    def _ajusta_um(self, X, y, X_val, y_val, semente):
        import lightgbm as lgb
        p = dict(self.PADRAO, **self.params, seed=semente, bagging_seed=semente,
                 feature_fraction_seed=semente, data_random_seed=semente)
        cats = colunas_categoricas(X)
        treino = lgb.Dataset(X, y, categorical_feature=cats)
        cb, val = [], None
        if X_val is not None:
            val = [lgb.Dataset(X_val, y_val, categorical_feature=cats)]
            cb = [lgb.early_stopping(self.paciencia, verbose=False)]
        return lgb.train(p, treino, num_boost_round=self.n_arvores,
                         valid_sets=val, callbacks=cb)

    def _preve_um(self, m, X):
        return m.predict(X, num_iteration=m.best_iteration)


class XGB(Modelo):
    nome_base = "xgb"
    PADRAO = dict(objective="reg:absoluteerror", eta=0.03, max_depth=6,
                  min_child_weight=5.0, subsample=0.8, colsample_bytree=0.6,
                  reg_lambda=5.0, tree_method="hist", max_cat_to_onehot=8)

    def _ajusta_um(self, X, y, X_val, y_val, semente):
        import xgboost as xgb
        p = dict(self.PADRAO, **self.params, seed=semente)
        # `enable_categorical` faz o XGBoost particionar categórica nativamente
        # em vez de exigir one-hot -- necessário com bairro de 183 níveis.
        dtr = xgb.DMatrix(X, y, enable_categorical=True)
        args = dict(params=p, dtrain=dtr, num_boost_round=self.n_arvores,
                    verbose_eval=False)
        if X_val is not None:
            args["evals"] = [(xgb.DMatrix(X_val, y_val, enable_categorical=True), "v")]
            args["early_stopping_rounds"] = self.paciencia
        return xgb.train(**args)

    def _preve_um(self, m, X):
        import xgboost as xgb
        lim = getattr(m, "best_iteration", None)
        d = xgb.DMatrix(X, enable_categorical=True)
        return m.predict(d, iteration_range=(0, lim + 1) if lim is not None else None)


class Cat(Modelo):
    nome_base = "cat"
    PADRAO = dict(loss_function="MAE", learning_rate=0.03, depth=6,
                  l2_leaf_reg=5.0, verbose=False, allow_writing_files=False)

    def _prepara(self, X, treino):
        # CatBoost quer categórica como string sem nulo -- ele trata o nível
        # "__ausente__" como categoria própria, que é o comportamento desejado:
        # não ter o dado é informação.
        Y = X.copy()
        for c in colunas_categoricas(Y):
            Y[c] = Y[c].astype(str).fillna("__ausente__").replace("nan", "__ausente__")
        for c in Y.columns:
            if Y[c].dtype == bool:
                Y[c] = Y[c].astype(np.int8)
        return Y

    def _ajusta_um(self, X, y, X_val, y_val, semente):
        from catboost import CatBoostRegressor, Pool
        cats = colunas_categoricas(X)
        p = dict(self.PADRAO, **self.params, random_seed=semente,
                 iterations=self.n_arvores)
        m = CatBoostRegressor(**p)
        val = Pool(X_val, y_val, cat_features=cats) if X_val is not None else None
        m.fit(Pool(X, y, cat_features=cats), eval_set=val,
              early_stopping_rounds=self.paciencia if val is not None else None,
              verbose=False)
        return m

    def _preve_um(self, m, X):
        return m.predict(X)


REGISTRO = {"lgbm": LGBM, "xgb": XGB, "cat": Cat}
