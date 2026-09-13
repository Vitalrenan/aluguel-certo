"""
Treino final e serialização do artefato — Arquitetura §3.

O que este script existe para consertar: **todo modelo já treinado neste projeto
foi descartado dentro do fold.** `executor.roda()` ajusta 25 vezes e joga fora;
não havia nada para servir. Aqui o modelo é ajustado uma vez, na base inteira
sem holdout, e gravado.

TRÊS MODOS, e a diferença entre eles é o orçamento de holdout:

    python treina_final.py                    # confere a montagem, não treina
    python treina_final.py --ablacao          # 25 folds; NUNCA toca o holdout
    python treina_final.py --write            # treina, mede no holdout UMA vez

O holdout é tocado só no terceiro. Se ele for medido durante busca de
configuração, deixa de existir para o projeto inteiro (Arquitetura §3), e por
isso `--ablacao` e `--write` são modos separados e não etapas do mesmo comando.

O ARTEFATO. Salvar só o booster não serve: ele não prevê sozinho. Precisa das
colunas na ordem exata (o LightGBM casa por posição) e das categorias vistas no
treino (bairro novo tem de virar nulo, não uma categoria inventada). Saem seis
arquivos em `modelos/{segmento}/{versao}/`:

    modelo.txt        booster do ponto central
    modelo_q10.txt    booster do quantil 10%   -- limite inferior do intervalo
    modelo_q90.txt    booster do quantil 90%   -- limite superior
    colunas.json      a ordem, que é parte do modelo
    categorias.json   os níveis de cada categórica
    metadados.json    proveniência, métricas de holdout e cobertura do intervalo

Os dois quantílicos são extensão declarada dos quatro arquivos da Arquitetura
§3. Ela deixou o intervalo em aberto entre duas vias -- quantil e faixa
empírica -- e registrou que a faixa empírica de largura constante estaria errada
nos extremos, porque a calibração medida tem viés de +40,5% no decil mais barato
e -15,5% no mais caro. Regressão quantílica dá largura que varia com o caso; a
cobertura que ela de fato entrega é medida aqui, no holdout, e vai para
`metadados.json`. Nenhuma faixa é prometida sem esse número.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import time
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comum.ml import avaliacao, dados, dedup, formulario, intervalo  # noqa: E402
from comum.ml.executor import Config, roda, _mascara_treino  # noqa: E402
from comum.ml.modelos import LGBM, colunas_categoricas  # noqa: E402

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RAIZ = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent.parent / "data/02_processed/listings/sp/santos"
MODELOS = RAIZ / "modelos"
DATA = "2026-09-07"
SEGMENTO = "venda_ate800k"
EXPERIMENTOS_DIR = Path(__file__).resolve().parent.parent / "data/03_experimentos"


def experimentos(segmento: str) -> Path:
    """
    Um arquivo de ablacao POR SEGMENTO.

    Era um caminho fixo. Com tres segmentos, a corrida de `locacao`
    sobrescreveria a de `venda_ate800k` e o `metadados.json` do modelo de venda
    passaria a citar a validacao cruzada do modelo de aluguel -- numero errado,
    com cara de certo, dentro do proprio artefato.
    """
    return EXPERIMENTOS_DIR / f"ablacao_formulario_{segmento}.json"

# Quantis do intervalo. 10/90 é 80% nominal de cobertura; o que ele entrega de
# fato é medido no holdout e reportado.
QUANTIS = (0.10, 0.90)


# --------------------------------------------------------------------------
# Treino
# --------------------------------------------------------------------------

def _split_interno(tr: pd.DataFrame, semente: int = 0):
    """
    Fatia de parada tirada do TREINO, 85/15 — a mesma de `executor.roda`.

    Usar o holdout aqui escolheria o número de árvores olhando o conjunto que
    existe para medir o resultado.
    """
    corte = int(len(tr) * 0.85)
    emb = np.random.default_rng(semente).permutation(len(tr))
    return tr.iloc[emb[:corte]], tr.iloc[emb[corte:]]


def _booster(X, y, X_val, y_val, params: dict | None = None, semente: int = 1000):
    import lightgbm as lgb

    p = dict(LGBM.PADRAO, **(params or {}), seed=semente, bagging_seed=semente,
             feature_fraction_seed=semente, data_random_seed=semente)
    cats = colunas_categoricas(X)
    val = lgb.Dataset(X_val, y_val, categorical_feature=cats)
    return lgb.train(p, lgb.Dataset(X, y, categorical_feature=cats),
                     num_boost_round=3000, valid_sets=[val],
                     callbacks=[lgb.early_stopping(100, verbose=False)])


class Artefato:
    """
    Booster central, os dois quantílicos, a ordem das colunas e as categorias.

    As categorias são fixadas no ajuste e reaplicadas na predição. Se cada
    chamada inferisse as próprias, o mesmo bairro viraria inteiros diferentes
    no treino e na inferência -- sem erro, sem aviso, e com previsão errada.
    """

    def __init__(self, colunas: list[str], categorias: dict[str, list[str]]):
        self.colunas = list(colunas)
        self.categorias = {c: list(v) for c, v in categorias.items()}
        self.central = None
        self.quantis: dict[str, object] = {}

    # -- preparação --------------------------------------------------------
    def prepara(self, X: pd.DataFrame) -> pd.DataFrame:
        faltam = [c for c in self.colunas if c not in X.columns]
        if faltam:
            raise ValueError(f"matriz sem as colunas do artefato: {faltam}")
        Y = X[self.colunas].copy()
        for c, niveis in self.categorias.items():
            dtype = pd.CategoricalDtype(niveis)
            # `.where(notna())` preserva o nulo; nível desconhecido vira nulo
            # por construção do CategoricalDtype -- que é o comportamento certo:
            # bairro que o treino não viu é ausência, não uma categoria nova.
            Y[c] = Y[c].astype(str).where(Y[c].notna()).astype(dtype)
        for c in Y.columns:
            if Y[c].dtype == bool:
                Y[c] = Y[c].astype(np.int8)
        return Y

    def preve(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        Xp = self.prepara(X)
        saida = {"central": self.central.predict(
            Xp, num_iteration=self.central.best_iteration)}
        for nome, m in self.quantis.items():
            saida[nome] = m.predict(Xp, num_iteration=m.best_iteration)
        return saida


def treina(tr: pd.DataFrame, colunas: list[str], dropout: float = 0.0,
           semente: int = 0) -> Artefato:
    """Ajusta o booster central e os dois quantílicos na mesma fatia."""
    X_tudo = formulario.matriz(tr, colunas)
    categorias = {c: sorted(X_tudo[c].dropna().astype(str).unique())
                  for c in colunas_categoricas(X_tudo)}
    art = Artefato(colunas, categorias)

    aj, pa = _split_interno(tr, semente)
    X_aj = _mascara_treino(formulario.matriz(aj, colunas), dropout, semente)
    X_pa = _mascara_treino(formulario.matriz(pa, colunas), dropout, semente + 1)
    y_aj, y_pa = aj.log_price.values, pa.log_price.values

    art.central = _booster(art.prepara(X_aj), y_aj, art.prepara(X_pa), y_pa)
    for q in QUANTIS:
        art.quantis[f"q{int(q * 100)}"] = _booster(
            art.prepara(X_aj), y_aj, art.prepara(X_pa), y_pa,
            params=dict(objective="quantile", alpha=q, metric="quantile"))
    return art


# --------------------------------------------------------------------------
# Medição no holdout — UMA vez, no `--write`
# --------------------------------------------------------------------------

def mede(art: Artefato, ho: pd.DataFrame, cenario: str) -> dict:
    X = avaliacao.apaga_campos(formulario.matriz(ho, art.colunas),
                               avaliacao.CENARIOS[cenario])
    p = art.preve(X)
    m = avaliacao.metricas(ho.log_price.values, p["central"], ho.price.values)
    # A cobertura é medida sobre A FAIXA QUE O SERVIÇO ENTREGA, montada pela
    # mesma função que ele usa. Medi-la sobre os quantis crus daria um número
    # de outra faixa -- plausível, publicado em `metadados.json`, e errado.
    faixa = intervalo.monta(np.exp(p["central"]), np.exp(p["q10"]),
                            np.exp(p["q90"]))
    m |= {"n": int(len(ho))} | intervalo.cobertura(ho.price.values, faixa)
    return m


def calibracao(art: Artefato, ho: pd.DataFrame, cenario: str) -> list[dict]:
    """Viés por decil de preço real — a compressão medida em Ajustes §3."""
    X = avaliacao.apaga_campos(formulario.matriz(ho, art.colunas),
                               avaliacao.CENARIOS[cenario])
    prev = np.exp(art.preve(X)["central"])
    real = ho.price.values
    decil = pd.qcut(pd.Series(real).rank(method="first"), 10, labels=False)
    saida = []
    for d in range(10):
        m = (decil == d).values
        saida.append({"decil": d + 1, "n": int(m.sum()),
                      "real_mediano": float(np.median(real[m])),
                      "previsto_mediano": float(np.median(prev[m])),
                      "vies_pct": float(np.median(prev[m]) / np.median(real[m]) - 1)})
    return saida


# --------------------------------------------------------------------------
# Ablação — 25 folds, sem holdout
# --------------------------------------------------------------------------

# Colunas que a maquinaria de avaliação precisa e que não são preditor.
# `_holdout` é filtrado por `roda`; `price`/`log_price` são alvo e estão em
# `dados.NAO_PREDITOR`.
MAQUINARIA = ("price", "log_price", "_holdout")


def recorta(d: pd.DataFrame) -> pd.DataFrame:
    """
    Deixa no quadro SÓ o formulário e a maquinaria.

    Existe porque `executor.roda` chama `dados.matriz`, que devolve toda coluna
    que não esteja em `NAO_PREDITOR` -- as 189 de amenidade, `familia`,
    `em_obra`, `area_total_m2`, `cep_prefix`. Sem este recorte a ablação mede o
    modelo de 218 colunas achando que mede o formulário, e o número sai
    plausível: a primeira corrida deu RMSE 101.691, contra os 102.553 que
    MEDICOES §26 registrou para a base cheia. Nada levanta, e o relatório sai
    errado -- o modo de falha caro do CLAUDE.md, dentro da própria medição.
    """
    faltam = [c for c in MAQUINARIA if c not in d.columns]
    if faltam:
        raise formulario.MontagemInvalida(f"quadro sem {faltam}")
    return d[list(formulario.COLUNAS) + list(MAQUINARIA)].copy()


def ablacao(base: pd.DataFrame, bruto: pd.DataFrame, segmento: str) -> None:
    """
    Mede as escolhas de instrumento que este script faz e o §27/§28 não fixou.

    Três perguntas, todas pareadas nos MESMOS folds:

      1. normalizar `property_type` (78 níveis crus, com `apartamento` e
         `Apartamento` separados) muda o resultado?
      2. as derivadas de `dados.basicas()` pagam, no formulário de 22 colunas?
      3. quanto vale `dropout_opcionais` -- a varredura anterior mediu ruído
         (D-034) e o valor continua sem medição.

    Os folds saem do quadro NORMALIZADO nos dois braços de (1): normalizar muda
    `chave_de_grupo`, e braços com folds diferentes não são pareáveis.
    """
    from comum.ml import avaliacao as av

    base, bruto = recorta(base), recorta(bruto)
    ref = dados.segmenta(base, segmento)
    ref = ref[~ref._holdout].reset_index(drop=True)
    particoes = av.folds(ref, 5, 5)
    print(f"folds fixos: {len(particoes)} partições, n={len(ref)}\n")

    registro: list[dict] = []

    def corre(nome, quadro, **kw):
        t0 = time.time()
        cfg = Config(nome=nome, segmento=segmento, **kw)
        r = roda(cfg, quadro, particoes=particoes)
        print(f"  {nome:<34} RMSE {r.media():>9,.0f}  ±{r.desvio():>6,.0f}"
              f"  MAE {r.media('mae_brl'):>9,.0f}"
              f"  med {r.media('erro_mediano'):>5.1%}"
              f"  <20% {r.media('dentro_20pct'):>5.1%}"
              f"   [{time.time()-t0:.0f}s]", flush=True)
        registro.append(r.resumo() | {"nome": nome.strip(), "config": kw})
        return r

    print("-" * 108)
    print("1. property_type — normalizado (18 níveis) contra cru (78)")
    print("-" * 108)
    a = corre("normalizado", base, features_basicas=False)
    b = corre("cru", bruto, features_basicas=False)
    _compara(a, b, "cru contra normalizado")

    print("\n" + "-" * 108)
    print("2. derivadas de dados.basicas() sobre o formulário")
    print("-" * 108)
    c = corre("com basicas", base, features_basicas=True)
    _compara(a, c, "com basicas contra sem")

    melhor, nome_melhor = (c, "com basicas") if c.media() < a.media() else (a, "sem basicas")
    print(f"\n  -> segue para (3) o braço vencedor: {nome_melhor}")

    print("\n" + "-" * 108)
    print("3. dropout_opcionais — a varredura anterior mediu ruído (D-034)")
    print("-" * 108)
    print("   Medido nos DOIS regimes. O cenário de decisão sozinho escolheria")
    print("   sempre o dropout mais alto, e ele é o que ensina o modelo a")
    print("   ignorar condomínio e IPTU -- punindo justamente quem os informa.")
    usa_basicas = nome_melhor == "com basicas"
    base_por_cenario: dict[str, object] = {}
    for cenario in ("sem_opcionais", "completo"):
        print(f"\n   cenário {cenario}:")
        for fracao in (0.0, 0.25, 0.50, 0.75, 1.0):
            r = corre(f"  dropout {fracao:.2f}", base,
                      features_basicas=usa_basicas, dropout_opcionais=fracao,
                      cenario=cenario)
            if fracao == 0.0:
                base_por_cenario[cenario] = r
            else:
                _compara(base_por_cenario[cenario], r,
                         f"dropout {fracao:.2f} contra 0,00")

    # O resultado é GRAVADO, não só impresso. `--write` o lê e o embute em
    # `metadados.json`, para que o artefato carregue a validação cruzada do seu
    # próprio instrumento em vez de um número copiado de documento.
    destino = experimentos(segmento)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps({"medido_em": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
            "segmento": segmento, "n": len(ref), "n_folds": len(particoes),
            "colunas": list(formulario.COLUNAS), "bracos": registro},
            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  gravado em {destino}")


def _compara(campeao, desafiante, rotulo: str) -> None:
    c = avaliacao.compara(campeao, desafiante)
    sinal = "melhora" if c["delta"] < 0 else "piora"
    print(f"     {rotulo:<38} {c['delta']:>+9,.0f} R$  "
          f"{c['venceu_em']}/{c['de']} folds  p={c['p']:.2g}  ({sinal})")


# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------

def _ablacao_gravada(segmento: str) -> dict | None:
    """
    A ablação deste repositório, se ela foi rodada. `None` diz que não foi.

    `None` é informação: significa que o artefato foi treinado sem validação
    cruzada do próprio instrumento, e quem lê os metadados fica sabendo em vez
    de supor.
    """
    caminho = experimentos(segmento)
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def _commit() -> str | None:
    """
    O SHA do pipeline, ou `None`.

    `git rev-parse HEAD` num repositório SEM commit nenhum imprime a string
    literal `HEAD` na saída padrão e o erro na de erro, com código != 0. A
    primeira versão desta função devolvia `r.stdout.strip()` e gravou
    `"commit": "HEAD"` em `metadados.json` -- proveniência falsa, com cara de
    verdadeira. Daí a checagem do código de saída E do formato.
    """
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=10,
                           cwd=Path(__file__).resolve().parent)
    except Exception:
        return None
    sha = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return None
    return sha


def _versao(destino: Path) -> str:
    """`2026-09-07-a`, `-b`, ... Nunca sobrescreve uma versão já gravada."""
    hoje = date.today().isoformat()
    for letra in "abcdefghijklmnopqrstuvwxyz":
        v = f"{hoje}-{letra}"
        if not (destino / v).exists():
            return v
    raise RuntimeError("26 versões no mesmo dia; escolha o nome à mão")


def grava(art: Artefato, destino: Path, metadados: dict) -> Path:
    """
    Escreve os seis arquivos. Falha de escrita LEVANTA — nunca é engolida.

    Grava em diretório temporário e renomeia no fim: um artefato pela metade
    seria carregado pelo serviço sem reclamar de nada.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    if parcial.exists():
        raise RuntimeError(f"{parcial} já existe — resto de gravação anterior")
    parcial.mkdir(parents=True)
    art.central.save_model(str(parcial / "modelo.txt"))
    for nome, m in art.quantis.items():
        m.save_model(str(parcial / f"modelo_{nome}.txt"))
    (parcial / "colunas.json").write_text(
        json.dumps(art.colunas, ensure_ascii=False, indent=2), encoding="utf-8")
    (parcial / "categorias.json").write_text(
        json.dumps(art.categorias, ensure_ascii=False, indent=2), encoding="utf-8")
    (parcial / "metadados.json").write_text(
        json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8")
    parcial.rename(destino)
    return destino


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--base", default=str(BASE))
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--segmento", default=SEGMENTO)
    ap.add_argument("--dropout", type=float, default=0.0,
                    help="dropout_opcionais do treino; 0 reproduz o instrumento "
                         "de MEDICOES §27/§28")
    ap.add_argument("--basicas", action="store_true",
                    help="acrescenta as derivadas de dados.basicas()")
    ap.add_argument("--ablacao", action="store_true",
                    help="mede as escolhas de instrumento; não toca o holdout")
    ap.add_argument("--write", action="store_true",
                    help="treina, mede no holdout UMA vez e serializa")
    ap.add_argument("--destino", default=str(MODELOS))
    args = ap.parse_args(argv)

    if args.ablacao and args.write:
        print("--ablacao e --write são exclusivos: o holdout não entra em busca "
              "de configuração", file=sys.stderr)
        return 2

    base_dir = Path(args.base)
    print(f"montando formulário de {base_dir}  data={args.data}")
    d = formulario.monta_base(base_dir, args.data)
    seg = dados.segmenta(d, args.segmento)
    print(f"  base            {len(d):>7} linhas x {d.shape[1]} colunas")
    print(f"  segmento {args.segmento:<14} {len(seg):>7} linhas "
          f"({int((~seg._holdout).sum())} treino + {int(seg._holdout.sum())} holdout)")
    print(f"  colunas do modelo   {len(formulario.COLUNAS):>3}  "
          f"({len(formulario.CATEGORICAS)} categóricas, "
          f"{len(formulario.NUMERICAS)} numéricas, "
          f"{len(formulario.BOOLEANAS)} booleanas)")
    tr_seg = seg[~seg._holdout]
    print("\n  preenchimento por coluna, no treino do segmento:")
    for c in formulario.COLUNAS:
        pre = float(tr_seg[c].notna().mean())
        extra = ""
        if c in formulario.BOOLEANAS:
            extra = f"   prevalência {tr_seg[c].mean():6.2%}"
        elif c in formulario.CATEGORICAS:
            extra = f"   {tr_seg[c].nunique()} níveis"
        print(f"    {c:<22} {pre:7.1%}{extra}")

    if args.ablacao:
        print("\n" + "=" * 108)
        print("ABLAÇÃO — 25 folds pareados, holdout intocado")
        print("=" * 108)
        bruto = formulario.monta_base(base_dir, args.data,
                                      normaliza_property_type=False)
        ablacao(d, bruto, args.segmento)
        return 0

    if not args.write:
        print("\nsem --write: nada foi treinado nem gravado.")
        print("  --ablacao  mede as escolhas de instrumento (25 folds)")
        print("  --write    treina, mede no holdout UMA vez e serializa")
        return 0

    # ---- treino final -----------------------------------------------------
    print("\n" + "=" * 108)
    print(f"TREINO FINAL  segmento={args.segmento}  dropout={args.dropout}  "
          f"basicas={args.basicas}")
    print("=" * 108)
    # O MESMO recorte da ablação. Sem ele, `dados.basicas` contaria
    # `n_amenidades` sobre as 189 colunas de amenidade da base cheia, e o
    # artefato sairia treinado num quadro que nenhuma medição cobriu.
    seg = recorta(seg)
    colunas = list(formulario.COLUNAS)
    if args.basicas:
        antes = set(seg.columns)
        seg = dados.basicas(seg)
        colunas += [c for c in dados.matriz(seg).columns if c not in antes]
    tr = seg[~seg._holdout].reset_index(drop=True)
    ho = dedup.funde(seg[seg._holdout]).reset_index(drop=True)
    t0 = time.time()
    art = treina(tr, colunas, dropout=args.dropout)
    print(f"  ajustado em {time.time()-t0:.0f}s   "
          f"{art.central.num_trees()} árvores (central), "
          f"{art.quantis['q10'].num_trees()} / {art.quantis['q90'].num_trees()} "
          f"(q10/q90)")

    # ---- holdout: a única vez ---------------------------------------------
    print(f"\n  HOLDOUT — {len(ho)} imóveis, tocado uma vez")
    metricas = {c: mede(art, ho, c) for c in ("sem_opcionais", "completo")}
    for cenario, m in metricas.items():
        print(f"    {cenario:<14} RMSE {m['rmse_brl']:>10,.0f}   "
              f"MAE {m['mae_brl']:>9,.0f}   med {m['erro_mediano']:>5.1%}   "
              f"<20% {m['dentro_20pct']:>5.1%}   "
              f"intervalo cobre {m['cobertura_intervalo']:>5.1%} "
              f"(largura mediana {m['largura_mediana_pct']:.0%}, "
              f"{m['cruzamentos']} cruzamentos)")
    # O viés sai em linha própria porque é a leitura COM SINAL, e misturá-lo
    # com as absolutas convida a lê-lo como magnitude.
    print("\n  VIÉS — sinal positivo é superestimar, negativo é subestimar:")
    for cenario, m in metricas.items():
        print(f"    {cenario:<14} carteira {m['vies_brl']:>+10,.0f} R$/imóvel   "
              f"médio {m['vies_pct']:>+6.1%}   mediano {m['vies_mediano']:>+6.1%}")

    print("\n  calibração no holdout, cenário de decisão:")
    for lin in calibracao(art, ho, "sem_opcionais"):
        print(f"    decil {lin['decil']:>2}  n={lin['n']:>4}  "
              f"real {lin['real_mediano']:>10,.0f}  ->  "
              f"previsto {lin['previsto_mediano']:>10,.0f}   "
              f"{lin['vies_pct']:>+6.1%}")

    import lightgbm as lgb
    meta = {
        "versao": None,
        "segmento": args.segmento,
        "base": f"P03_{args.data}",
        "fontes": [f"P01_{args.data}", f"P02_{args.data}", f"P03_{args.data}"],
        "treinado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _commit(),
        "n_treino": int(len(tr)),
        "n_holdout": int(len(ho)),
        "dropout_opcionais": args.dropout,
        "features_basicas": bool(args.basicas),
        "cenario_de_decisao": avaliacao.CENARIO_DECISAO,
        "opcionais": list(avaliacao.OPCIONAIS),
        "quantis": list(QUANTIS),
        "perguntas": [
            {"api": p.api, "rotulo": p.rotulo, "tipo": p.tipo,
             "colunas": list(p.colunas), "obrigatoria": p.obrigatoria,
             "faixa": list(p.faixa) if p.faixa else None,
             "delta_rmse": p.delta_rmse, "fonte": p.fonte, "nota": p.nota}
            for p in formulario.PERGUNTAS],
        "familias": {k: list(v) for k, v in formulario.FAMILIAS.items()},
        # O `select` de bairro só é utilizável filtrado: o segmento tem 799
        # níveis de `neighborhood` em 17 cidades, porque o recorte "Santos" na
        # verdade traz Santos, a região e São Paulo. Sem este mapa o formulário
        # ofereceria os 799 de uma vez.
        "bairros_por_cidade": {
            str(c): sorted(g.dropna().astype(str).unique())
            for c, g in tr.groupby(tr.city.astype(str)).neighborhood},
        "prevalencia_familias": {
            k: round(float(tr[k].mean()) * 100, 2) for k in formulario.FAMILIAS},
        "holdout": metricas,
        "calibracao_holdout": calibracao(art, ho, "sem_opcionais"),
        # Duas referências, e elas NÃO são a mesma coisa.
        "validacao_cruzada": _ablacao_gravada(args.segmento),
        "referencia_publicada": {
            "fonte": "MEDICOES §28, 25 folds, n=6.974, colunas cruas",
            "rmse_brl_sem_opcionais": 96953,
            "rmse_brl_completo": 91717,
            "ressalva": "o script que produziu estes números não ficou salvo. "
                        "Medido pelo caminho do `executor`, o mesmo formulário "
                        "dá outro nível no cenário de decisão. Ver `validacao_"
                        "cruzada` acima, que é medida por este repositório.",
        },
        "ambiente": {"python": platform.python_version(),
                     "lightgbm": lgb.__version__,
                     "pandas": pd.__version__, "numpy": np.__version__},
    }

    destino_raiz = Path(args.destino) / args.segmento
    versao = _versao(destino_raiz)
    meta["versao"] = versao
    caminho = grava(art, destino_raiz / versao, meta)
    print(f"\n  gravado em {caminho}")
    for f in sorted(caminho.iterdir()):
        print(f"    {f.name:<20} {f.stat().st_size:>9,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
