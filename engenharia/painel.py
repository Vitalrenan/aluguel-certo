"""
Painel administrativo da coleta — HTML local, lido do lago.

    python painel.py                 # gera e abre
    python painel.py --saida x.html

INTERNO E LOCAL, por decisão. Não vai para o Cloud Run e não tem autenticação
porque não tem por que estar exposto: ele mostra a saúde de processo e a
contagem por cidade, que é informação de operação e não de produto. Publicá-lo
custaria um serviço a mais e uma superfície a mais para proteger.

LÊ O DISCO, não a API. O painel existe justamente para responder "o que a
coleta produziu", e perguntar isso ao serviço que consome a coleta mediria o
consumo, não a produção.

TRÊS PERGUNTAS, e o painel não responde outras:

  1. Quais das quinze cidades-alvo já têm anúncio, e quantos
  2. Quantos de venda e quantos de locação, por cidade
  3. Onde a esteira está quebrada -- camada vazia, mês sem coleta, campo que
     despencou de preenchimento

O QUE ELE NÃO FAZ: não estima, não treina e não escreve no lago. Um painel que
escreve vira etapa de pipeline disfarçada de relatório.
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from comum.cidades_alvo import CIDADES, Estado, alvo

RAIZ = Path(__file__).resolve().parent
SAIDA_PADRAO = RAIZ / "painel.html"


# ---------------------------------------------------------------------------
# medição
# ---------------------------------------------------------------------------

def le_bruto(raiz: Path) -> pd.DataFrame:
    """Uma linha por partição da camada crua de anúncios."""
    linhas = []
    for p in (raiz / "01_raw" / "listings").glob("*/*/*/*/*/*/*/*.parquet"):
        partes = p.relative_to(raiz / "01_raw" / "listings").parts
        if len(partes) != 8:
            continue
        uf, cidade, ano, mes, dia, plataforma, dominio, _ = partes
        try:
            n = len(pd.read_parquet(p, columns=["property_id"]))
        except Exception:
            n = -1        # ilegível é diferente de vazio, e aparece assim
        linhas.append({"uf": uf, "cidade": cidade, "ano": ano, "mes": mes,
                       "dia": dia, "plataforma": plataforma,
                       "dominio": dominio, "linhas": n,
                       "mes_referencia": f"{ano}-{mes}"})
    return pd.DataFrame(linhas)


def le_historico(raiz: Path) -> pd.DataFrame:
    arquivos = sorted((raiz / "02_processed" / "listings")
                      .glob("*/*/ano=*/mes=*/historico.parquet"))
    if not arquivos:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(a) for a in arquivos], ignore_index=True)


def saude(raiz: Path, bruto: pd.DataFrame, hist: pd.DataFrame,
          modelos: Path) -> list[dict]:
    """
    O que está quebrado, dito como frase e não como código de erro.

    Cada item tem severidade e o que fazer. Um painel que só mostra número
    deixa a leitura por conta de quem olha, e quem olha nem sempre sabe qual
    número é ruim.
    """
    itens = []

    def add(ok: bool, titulo: str, detalhe: str, acao: str = ""):
        itens.append({"ok": ok, "titulo": titulo, "detalhe": detalhe,
                      "acao": acao})

    # camadas
    for nome, padrao in (("crua", "01_raw/listings/*/*/*/*/*/*/*/*.parquet"),
                         ("tratada", "02_processed/listings/*/*/ano=*/mes=*/*.parquet"),
                         ("refinada", "03_refined/mercado/*.parquet")):
        n = len(glob.glob(str(raiz / padrao)))
        add(n > 0, f"camada {nome}", f"{n} arquivo(s)",
            "" if n else f"a camada {nome} está vazia -- o estágio não rodou")

    # partições ilegíveis
    if len(bruto):
        ruins = bruto[bruto.linhas < 0]
        add(not len(ruins), "partições legíveis",
            f"{len(ruins)} ilegível(is) de {len(bruto)}",
            "parquet corrompido não pode virar partição vazia em silêncio"
            if len(ruins) else "")

        vazias = bruto[bruto.linhas == 0]
        add(not len(vazias), "partições com linha",
            f"{len(vazias)} vazia(s) de {len(bruto)}",
            "zero linha é falha, não sucesso" if len(vazias) else "")

    # meses sem coleta
    if len(bruto):
        meses = sorted(bruto.mes_referencia.unique())
        atual = datetime.now(timezone.utc).strftime("%Y-%m")
        add(atual in meses, "coleta do mês corrente",
            f"último mês coletado: {meses[-1]}",
            f"não há coleta de {atual} -- a série mensal fica com buraco"
            if atual not in meses else "")

    # modelos
    cartoes = list(modelos.glob("*/*/*/*/model_card.json"))
    sem_cartao = [p.parent for p in modelos.glob("*/*/*/*/modelo.txt")
                  if not (p.parent / "model_card.json").exists()]
    add(not sem_cartao, "todo modelo tem cartão",
        f"{len(cartoes)} modelo(s), {len(sem_cartao)} sem cartão",
        "a API pula diretório sem cartão e responde 'modelo ausente'"
        if sem_cartao else "")

    # preenchimento
    if len(hist):
        criticos = ["price", "area_m2", "neighborhood", "city"]
        baixos = {c: hist[c].notna().mean() for c in criticos
                  if c in hist and hist[c].notna().mean() < 0.90}
        add(not baixos, "campos críticos preenchidos",
            ", ".join(f"{c} {v:.0%}" for c, v in baixos.items()) or
            "price, área, bairro e cidade acima de 90%",
            "campo crítico abaixo de 90% costuma ser erro nosso, não ausência "
            "na fonte" if baixos else "")

    return itens


def por_cidade(hist: pd.DataFrame, bruto: pd.DataFrame) -> list[dict]:
    """Uma linha por cidade-alvo, coletada ou não."""
    venda = locacao = {}
    if len(hist):
        g = hist.groupby(["city", "transaction_type"]).size()
        venda = {k[0]: v for k, v in g.items() if k[1] == "venda"}
        locacao = {k[0]: v for k, v in g.items() if k[1] == "locacao"}

    # do lago cru, por slug de caminho
    bruto_por_cidade = (bruto.groupby("cidade").linhas.sum().to_dict()
                        if len(bruto) else {})
    dominios = (bruto.groupby("cidade").dominio.nunique().to_dict()
                if len(bruto) else {})
    ultima = (bruto.groupby("cidade")
              .apply(lambda d: f"{d.ano.max()}-{d[d.ano == d.ano.max()].mes.max()}",
                     include_groups=False).to_dict() if len(bruto) else {})

    def casa(nome_hist: str) -> str | None:
        c = alvo(nome_hist)
        return c.slug if c else None

    v_slug, l_slug = defaultdict(int), defaultdict(int)
    for nome, n in venda.items():
        s = casa(nome)
        if s:
            v_slug[s] += int(n)
    for nome, n in locacao.items():
        s = casa(nome)
        if s:
            l_slug[s] += int(n)

    linhas = []
    for c in CIDADES:
        linhas.append({
            "nome": c.nome, "uf": c.uf, "slug": c.slug,
            "estado": c.estado.value,
            "venda": v_slug.get(c.slug, 0),
            "locacao": l_slug.get(c.slug, 0),
            "bruto": int(bruto_por_cidade.get(c.slug, 0)),
            "dominios": int(dominios.get(c.slug, 0)),
            "ultima_coleta": ultima.get(c.slug, "—"),
            "nota": c.nota,
        })
    return linhas


# ---------------------------------------------------------------------------
# desenho
# ---------------------------------------------------------------------------

def _barra(n: int, teto: int, largura: int = 120) -> int:
    return 0 if not teto else max(2, round(largura * n / teto))


def desenha(cidades: list[dict], itens: list[dict], bruto: pd.DataFrame,
            hist: pd.DataFrame) -> str:
    e = html.escape
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    teto = max([c["venda"] + c["locacao"] for c in cidades] + [1])

    total_v = sum(c["venda"] for c in cidades)
    total_l = sum(c["locacao"] for c in cidades)
    coletando = sum(1 for c in cidades if c["venda"] + c["locacao"] > 0)
    problemas = [i for i in itens if not i["ok"]]

    def linha_cidade(c: dict) -> str:
        tem = c["venda"] + c["locacao"] > 0
        return f"""
      <tr class="{'tem' if tem else 'vazia'}">
        <td class="cid"><b>{e(c['nome'])}</b> <span class="uf">{e(c['uf'])}</span></td>
        <td><span class="pill pill--{e(c['estado'])}">{e(c['estado'].replace('_', ' '))}</span></td>
        <td class="num">{c['venda']:,}</td>
        <td class="num">{c['locacao']:,}</td>
        <td class="barra">
          <i style="width:{_barra(c['venda'], teto)}px" class="b-v"></i><i
             style="width:{_barra(c['locacao'], teto)}px" class="b-l"></i>
        </td>
        <td class="num">{c['dominios'] or '—'}</td>
        <td class="mono">{e(c['ultima_coleta'])}</td>
      </tr>"""

    def linha_saude(i: dict) -> str:
        return f"""
      <li class="{'ok' if i['ok'] else 'ruim'}">
        <b>{e(i['titulo'])}</b>
        <span>{e(i['detalhe'])}</span>
        {f'<em>{e(i["acao"])}</em>' if i['acao'] else ''}
      </li>"""

    plataformas = ""
    if len(bruto):
        g = (bruto.groupby("plataforma")
             .agg(particoes=("linhas", "size"), linhas=("linhas", "sum"),
                  dominios=("dominio", "nunique"))
             .sort_values("linhas", ascending=False))
        plataformas = "".join(
            f"<tr><td>{e(p)}</td><td class='num'>{int(r.dominios)}</td>"
            f"<td class='num'>{int(r.particoes)}</td>"
            f"<td class='num'>{int(r.linhas):,}</td></tr>"
            for p, r in g.iterrows())

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel da coleta — Aluguel Certo</title>
<style>
  :root {{
    --ground:#EFF3FA; --surface:#fff; --mute:#F5F8FD; --sunk:#E8EEF9;
    --ink:#15203B; --soft:#586686; --faint:#93A0BC;
    --brand:#2563EB; --wash:#E7EEFD; --ciano:#0891B2; --linha:#E6EBF5;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  }}
  @media (prefers-color-scheme:dark) {{
    :root {{ --ground:#0A1120; --surface:#131D31; --mute:#18233C; --sunk:#0F1A2C;
      --ink:#E5EBF7; --soft:#9FACC8; --faint:#6C7B9A; --brand:#5F91F2;
      --wash:#1A2A49; --ciano:#3CBCD9; --linha:#24314C; }}
  }}
  *,*::before,*::after{{box-sizing:border-box}}
  body{{margin:0;background:var(--ground);color:var(--ink);
    font:15px/1.6 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}}
  .env{{max-width:1080px;margin:0 auto;padding:0 20px}}
  header{{background:var(--surface);border-bottom:1px solid var(--linha);
    padding-block:30px 26px;margin-bottom:34px}}
  h1{{margin:0 0 6px;font-size:27px;letter-spacing:-.02em}}
  .selo{{font-family:var(--mono);font-size:11px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--faint)}}
  .sub{{color:var(--soft);font-size:14.5px;margin:0}}
  .placar{{display:flex;flex-wrap:wrap;gap:0;border:1px solid var(--linha);
    border-radius:12px;overflow:hidden;background:var(--mute);margin-top:20px}}
  .placar div{{flex:1 1 130px;padding:13px 18px;border-left:1px solid var(--linha)}}
  .placar div:first-child{{border-left:0}}
  .placar dt{{font-family:var(--mono);font-size:10px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--faint);margin-bottom:3px}}
  .placar dd{{margin:0;font-size:22px;font-weight:600;
    font-variant-numeric:tabular-nums;letter-spacing:-.02em}}
  h2{{font-size:19px;margin:36px 0 4px;letter-spacing:-.01em}}
  .nota{{color:var(--soft);font-size:13.5px;margin:0 0 14px;max-width:70ch}}
  .caixa{{background:var(--surface);border:1px solid var(--linha);
    border-radius:12px;overflow-x:auto}}
  table{{border-collapse:collapse;width:100%;font-size:13.6px}}
  th{{font-family:var(--mono);font-size:10px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--faint);text-align:left;
    padding:11px 14px;border-bottom:1px solid var(--linha);white-space:nowrap}}
  td{{padding:9px 14px;border-top:1px solid var(--linha);color:var(--soft);
    vertical-align:middle}}
  tr:first-child td{{border-top:0}}
  .num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .mono{{font-family:var(--mono);font-size:12px}}
  .cid b{{color:var(--ink);font-weight:600}}
  .uf{{color:var(--faint);font-size:11.5px;font-family:var(--mono)}}
  tr.vazia td{{opacity:.55}}
  .barra{{width:140px}}
  .barra i{{display:inline-block;height:9px;border-radius:2px;vertical-align:middle}}
  .b-v{{background:var(--brand)}} .b-l{{background:var(--ciano)}}
  .pill{{font-family:var(--mono);font-size:10px;letter-spacing:.07em;
    text-transform:uppercase;padding:3px 8px;border-radius:999px;
    border:1px solid var(--linha);color:var(--soft);white-space:nowrap}}
  .pill--coletando{{border-color:var(--brand);color:var(--brand);background:var(--wash)}}
  .pill--a_inventariar{{border-style:dotted}}
  ul.saude{{list-style:none;padding:0;margin:0;display:flex;
    flex-direction:column;gap:1px;background:var(--linha);
    border:1px solid var(--linha);border-radius:12px;overflow:hidden}}
  /* FLEX, não grid: no grid cada filho vira uma célula e o título, o detalhe
     e a ação empilhavam em três linhas. Aqui os três fluem na mesma linha e
     só a ação quebra, que é onde a quebra ajuda a leitura. */
  ul.saude li{{background:var(--surface);padding:12px 16px;display:flex;
    flex-wrap:wrap;align-items:baseline;gap:0 9px}}
  ul.saude li::before{{content:"ok";font-family:var(--mono);font-size:10px;
    color:var(--faint);letter-spacing:.06em;flex:none;width:22px}}
  ul.saude li.ruim::before{{content:"!!";color:var(--brand);font-weight:700}}
  ul.saude li b{{color:var(--ink);font-weight:600;font-size:14px}}
  ul.saude li span{{color:var(--soft);font-size:13.5px}}
  ul.saude li em{{flex-basis:100%;margin-left:31px;color:var(--brand);
    font-style:normal;font-size:13px;margin-top:3px}}
  .legenda{{display:flex;gap:20px;font-family:var(--mono);font-size:11.5px;
    color:var(--soft);padding-top:10px}}
  .legenda i{{display:inline-block;width:16px;height:9px;border-radius:2px;
    margin-right:6px;vertical-align:middle}}
  footer{{margin-top:44px;padding-block:20px 30px;border-top:1px solid var(--linha);
    font-family:var(--mono);font-size:11.5px;color:var(--faint)}}
</style></head><body>

<header><div class="env">
  <div class="selo">Painel interno da coleta</div>
  <h1>Aluguel Certo — esteira</h1>
  <p class="sub">Lido do disco em {agora}. Roda local, não vai para a nuvem.</p>
  <dl class="placar">
    <div><dt>cidades-alvo</dt><dd>{len(cidades)}</dd></div>
    <div><dt>com anúncio</dt><dd>{coletando}</dd></div>
    <div><dt>à venda</dt><dd>{total_v:,}</dd></div>
    <div><dt>para alugar</dt><dd>{total_l:,}</dd></div>
    <div><dt>alertas</dt><dd>{len(problemas)}</dd></div>
  </dl>
</div></header>

<main class="env">
  <h2>Saúde da esteira</h2>
  <p class="nota">Cada item diz o que está medido e, quando está ruim, o que
  isso causa. Número sem leitura deixa o diagnóstico por conta de quem olha.</p>
  <ul class="saude">{''.join(linha_saude(i) for i in itens)}</ul>

  <h2>As {len(cidades)} cidades-alvo</h2>
  <p class="nota">A lista é fechada: coleta acontece nestas e em mais nenhuma.
  Cidade sem anúncio aparece apagada, e não sumida — o que falta coletar é
  informação de operação tanto quanto o que já foi.</p>
  <div class="caixa"><table>
    <thead><tr>
      <th>cidade</th><th>estado</th><th class="num">venda</th>
      <th class="num">locação</th><th>proporção</th>
      <th class="num">domínios</th><th>última coleta</th>
    </tr></thead>
    <tbody>{''.join(linha_cidade(c) for c in cidades)}</tbody>
  </table></div>
  <div class="legenda">
    <span><i class="b-v"></i>venda</span><span><i class="b-l"></i>locação</span>
  </div>

  {f'''<h2>Plataformas</h2>
  <p class="nota">O que cada adapter trouxe. Partição é um dia de um domínio.</p>
  <div class="caixa"><table>
    <thead><tr><th>plataforma</th><th class="num">domínios</th>
      <th class="num">partições</th><th class="num">linhas</th></tr></thead>
    <tbody>{plataformas}</tbody>
  </table></div>''' if plataformas else ''}
</main>

<footer class="env">
  Gerado por <code>engenharia/painel.py</code> · lê o disco, não a API ·
  não escreve no lago
</footer>
</body></html>"""


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dados", default=str(RAIZ / "dados"))
    ap.add_argument("--modelos", default=str(RAIZ / "modelos"))
    ap.add_argument("--saida", default=str(SAIDA_PADRAO))
    ap.add_argument("--sem-abrir", action="store_true")
    args = ap.parse_args(argv)

    dados = Path(args.dados)
    if not dados.exists():
        print(f"não existe: {dados}", file=sys.stderr)
        return 1

    bruto = le_bruto(dados)
    hist = le_historico(dados)
    itens = saude(dados, bruto, hist, Path(args.modelos))
    cidades = por_cidade(hist, bruto)

    alvo_arq = Path(args.saida)
    alvo_arq.write_text(desenha(cidades, itens, bruto, hist), encoding="utf-8")

    problemas = [i for i in itens if not i["ok"]]
    print(f"painel: {alvo_arq}")
    print(f"  {sum(1 for c in cidades if c['venda'] + c['locacao'] > 0)}"
          f" de {len(cidades)} cidades-alvo com anúncio")
    print(f"  {sum(c['venda'] for c in cidades):,} à venda, "
          f"{sum(c['locacao'] for c in cidades):,} para alugar")
    if problemas:
        print(f"  {len(problemas)} alerta(s):")
        for p in problemas:
            print(f"    {p['titulo']}: {p['detalhe']}")

    if not args.sem_abrir:
        webbrowser.open(alvo_arq.resolve().as_uri())
    # Alerta NÃO derruba o painel: ele existe para mostrar problema, e sair
    # diferente de zero faria o relatório de falha ser lido como falha do
    # relatório.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
