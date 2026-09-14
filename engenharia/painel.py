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
#
# O PADRÃO É O DO FRONTEND, e não um segundo. Cartão branco sobre cinza-gelo,
# separação por SOMBRA e nunca por borda, raio grande e uniforme, e a tela
# majoritariamente silenciosa -- `frontend/design-system.md` §1.
#
# UMA FAMÍLIA DE ACENTO SÓ, AZUL. O ciano é o único desvio de matiz, e ele
# também é azul. Aqui o estado de cada item é dito pelo RÓTULO, não pela cor:
# um painel de operação que depende de distinguir verde de vermelho falha para
# quem não distingue, e falha de noite para todo mundo.
# ---------------------------------------------------------------------------

def _barra(n: int, teto: int, largura: int = 132) -> float:
    """
    Zero é ZERO pixel, e não o mínimo visível.

    O piso de 3px existe para que uma cidade com poucas dezenas de anúncios não
    desapareça da barra. Aplicado também ao zero, ele desenhava um ponto azul em
    doze cidades que não têm anúncio nenhum -- o mesmo traço para "quase nada" e
    para "nada", que são estados diferentes desta esteira.
    """
    if not n or not teto:
        return 0
    return max(3, round(largura * n / teto, 1))


def desenha(cidades: list[dict], itens: list[dict], bruto: pd.DataFrame,
            hist: pd.DataFrame) -> str:
    e = html.escape
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    teto = max([c["venda"] + c["locacao"] for c in cidades] + [1])

    total_v = sum(c["venda"] for c in cidades)
    total_l = sum(c["locacao"] for c in cidades)
    coletando = sum(1 for c in cidades if c["venda"] + c["locacao"] > 0)
    problemas = [i for i in itens if not i["ok"]]
    particoes = len(bruto)

    def linha_cidade(c: dict) -> str:
        tem = c["venda"] + c["locacao"] > 0
        estado = c["estado"].replace("_", " ")
        return f"""
        <tr class="{'tem' if tem else 'vazia'}">
          <td><span class="cid">{e(c['nome'])}</span><span class="uf">{e(c['uf'])}</span></td>
          <td><span class="pilula pilula--{e(c['estado'])}">{e(estado)}</span></td>
          <td class="num">{c['venda']:,}</td>
          <td class="num">{c['locacao']:,}</td>
          <td class="prop">
            <span class="trilho">
              <i class="b-v" style="width:{_barra(c['venda'], teto)}px"></i><i
                 class="b-l" style="width:{_barra(c['locacao'], teto)}px"></i>
            </span>
          </td>
          <td class="num soft">{c['dominios'] or '—'}</td>
          <td class="num soft">{e(c['ultima_coleta'])}</td>
        </tr>"""

    def item_saude(i: dict) -> str:
        return f"""
        <li class="{'ok' if i['ok'] else 'ruim'}">
          <span class="marca">{'tudo certo' if i['ok'] else 'atenção'}</span>
          <div>
            <b>{e(i['titulo'])}</b> <span class="det">{e(i['detalhe'])}</span>
            {f'<p>{e(i["acao"])}</p>' if i['acao'] else ''}
          </div>
        </li>"""

    plataformas = ""
    if len(bruto):
        g = (bruto.groupby("plataforma")
             .agg(particoes=("linhas", "size"), linhas=("linhas", "sum"),
                  dominios=("dominio", "nunique"))
             .sort_values("linhas", ascending=False))
        teto_p = max(int(g.linhas.max()), 1)
        plataformas = "".join(
            f"""<tr>
              <td><span class="cid">{e(p)}</span></td>
              <td class="num soft">{int(r.dominios)}</td>
              <td class="num soft">{int(r.particoes)}</td>
              <td class="num">{int(r.linhas):,}</td>
              <td class="prop"><span class="trilho"><i class="b-v"
                 style="width:{_barra(int(r.linhas), teto_p)}px"></i></span></td>
            </tr>""" for p, r in g.iterrows())

    def kpi(rotulo: str, valor: str, nota: str = "") -> str:
        return f"""<div class="kpi">
          <span class="eyebrow">{e(rotulo)}</span>
          <strong>{e(valor)}</strong>
          {f'<span class="nota">{e(nota)}</span>' if nota else ''}
        </div>"""

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel da coleta — Aluguel Certo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap">
<style>
/* Tokens de frontend/app/globals.css. Fonte única -- um segundo conjunto aqui
   divergiria do produto na primeira mudança de paleta.

   CLARO POR DECISÃO, e não por falta de tema escuro. A referência do
   design-system.md é branca e silenciosa, e o efeito depende disso: a cor
   aparece em quatro lugares, e saturar o resto o destrói.

   `color-scheme: light` impede o navegador de escurecer barra de rolagem e
   controle nativo quando o sistema está no escuro -- sem isso a página fica
   clara com a rolagem preta, que é pior que qualquer um dos dois temas. */
:root {{
  color-scheme: light;
  --canvas:#EFF3FA; --surface:#FFFFFF; --surface-mute:#F5F8FD;
  --ink:#15203B; --ink-soft:#586686; --ink-faint:#93A0BC;
  --brand:#2563EB; --brand-soft:#E7EEFD; --brand-escuro:#1B3FA8;
  --ciano:#0891B2; --ciano-soft:#E2F4FA; --linha:#E6EBF5;
  --r-card:1.25rem; --r-ctrl:.75rem; --r-pill:9999px;
  /* DUAS sombras, não cinco. É o que faz a tela parecer calma (§2). */
  --sombra-card:0 1px 2px rgb(17 24 39/.04), 0 8px 24px -8px rgb(17 24 39/.08);
  --sombra-float:0 2px 4px rgb(17 24 39/.06), 0 16px 40px -12px rgb(17 24 39/.16);
}}
*,*::before,*::after{{box-sizing:border-box}}
body{{margin:0;background:var(--canvas);color:var(--ink);
  font:400 15px/1.6 Inter,ui-sans-serif,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}}
.nums,.num{{font-variant-numeric:tabular-nums}}
:focus-visible{{outline:2.5px solid var(--brand);outline-offset:3px;border-radius:8px}}

/* -------- leiaute: sidebar 240px + conteúdo, gutter 24, gap 16 -------- */
/* `minmax(0,1fr)` e nao `1fr`. Item de grid tem `min-width:auto`, entao o
   conteudo mais largo empurra a coluna alem da fracao e a pagina inteira passa
   a rolar de lado -- com a sidebar acompanhando, que e o pior sintoma. */
.app{{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}}
.lado{{background:var(--surface);border-right:1px solid var(--linha);
  padding:22px 16px;display:flex;flex-direction:column;gap:26px}}
.marca{{display:flex;align-items:center;gap:11px;padding:0 6px}}
.marca .av{{width:38px;height:38px;border-radius:11px;background:var(--brand);
  color:#fff;display:grid;place-items:center;font-weight:600;font-size:17px;flex:none}}
/* O seletor mira o span DE DENTRO do nome. Mirando `.marca span` ele
   pegava o wrapper também, e o `b` herdava o uppercase: a marca saía
   gritando em caixa alta. */
.marca .nome b{{display:block;font-weight:600;font-size:15px;line-height:1.25}}
.marca .nome span{{display:block;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-faint);margin-top:2px}}
.eyebrow{{font-size:11px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-faint)}}
.menu{{display:flex;flex-direction:column;gap:3px;margin-top:8px}}
.menu a{{display:flex;align-items:center;gap:10px;padding:9px 12px;
  border-radius:var(--r-ctrl);color:var(--ink-soft);text-decoration:none;
  font-size:14px}}
.menu a.on{{background:var(--brand-soft);color:var(--brand);font-weight:500}}
.menu a:hover:not(.on){{background:var(--surface-mute)}}
.menu i{{width:7px;height:7px;border-radius:2px;background:currentColor;
  opacity:.55;flex:none}}
.lado footer{{margin-top:auto;font-size:11.5px;color:var(--ink-faint);
  line-height:1.55;padding:0 6px}}

main{{padding:26px 24px 40px;max-width:1180px;min-width:0}}
.topo{{margin-bottom:20px}}
.topo h1{{margin:6px 0 4px;font-size:26px;font-weight:600;letter-spacing:-.02em}}
.topo p{{margin:0;color:var(--ink-soft);font-size:14px}}

.cartao{{background:var(--surface);border-radius:var(--r-card);
  box-shadow:var(--sombra-card);padding:24px;margin-bottom:16px}}
.cartao > h2{{margin:0 0 3px;font-size:18px;font-weight:500;letter-spacing:-.01em}}
.cartao > .sub{{margin:0 0 18px;color:var(--ink-soft);font-size:13.5px;max-width:70ch}}

/* -------- KPI -------- */
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));
  gap:16px;margin-bottom:16px}}
.kpi{{background:var(--surface);border-radius:var(--r-card);
  box-shadow:var(--sombra-card);padding:20px 22px;display:flex;
  flex-direction:column;gap:7px}}
.kpi strong{{font-size:30px;font-weight:600;letter-spacing:-.025em;
  font-variant-numeric:tabular-nums;line-height:1}}
.kpi .nota{{font-size:12px;color:var(--ink-faint)}}
.kpi--acento{{background:var(--brand);color:#fff}}
.kpi--acento .eyebrow,.kpi--acento .nota{{color:rgb(255 255 255/.72)}}

/* -------- saúde -------- */
ul.saude{{list-style:none;margin:0;padding:0;display:flex;
  flex-direction:column;gap:2px}}
ul.saude li{{display:grid;grid-template-columns:104px 1fr;gap:0 16px;
  align-items:start;padding:11px 14px;border-radius:var(--r-ctrl)}}
ul.saude li:nth-child(odd){{background:var(--surface-mute)}}
ul.saude li b{{font-weight:500;font-size:14.5px}}
ul.saude li .det{{color:var(--ink-soft);font-size:13.5px}}
ul.saude li p{{margin:4px 0 0;color:var(--brand);font-size:13px}}
/* O estado é dito pela PALAVRA. Cor sozinha não carrega significado aqui. */
ul.saude .marca{{font-size:11px;font-weight:600;letter-spacing:.05em;
  text-transform:uppercase;color:var(--ink-faint);padding-top:2px;
  display:block}}
ul.saude li.ruim .marca{{color:var(--brand)}}

/* -------- tabela -------- */
.rolagem{{overflow-x:auto;margin:0 -6px;padding:0 6px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th{{text-align:left;font-size:11px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-faint);padding:0 14px 11px;
  white-space:nowrap}}
td{{padding:11px 14px;border-top:1px solid var(--linha);color:var(--ink-soft);
  vertical-align:middle}}
.num{{text-align:right;white-space:nowrap;color:var(--ink)}}
.soft{{color:var(--ink-soft)}}
.cid{{color:var(--ink);font-weight:500;white-space:nowrap}}
.uf{{color:var(--ink-faint);font-size:11.5px;margin-left:7px;
  letter-spacing:.04em}}
tr.vazia .cid,tr.vazia .uf{{color:var(--ink-faint)}}
tr.vazia td{{color:var(--ink-faint)}}
.prop{{width:150px}}
.trilho{{display:inline-flex;align-items:center;height:8px;border-radius:99px;
  overflow:hidden;background:var(--surface-mute);min-width:4px}}
.trilho i{{display:block;height:8px}}
.b-v{{background:var(--brand)}} .b-l{{background:var(--ciano)}}
.pilula{{display:inline-block;font-size:11px;font-weight:500;padding:3px 10px;
  border-radius:var(--r-pill);background:var(--surface-mute);
  color:var(--ink-soft);white-space:nowrap}}
.pilula--coletando{{background:var(--brand-soft);color:var(--brand)}}
.legenda{{display:flex;gap:20px;font-size:12.5px;color:var(--ink-soft);
  padding-top:14px}}
.legenda i{{display:inline-block;width:18px;height:8px;border-radius:99px;
  margin-right:7px;vertical-align:middle}}

@media (max-width:860px){{
  .app{{grid-template-columns:1fr}}
  .lado{{border-right:0;border-bottom:1px solid var(--linha)}}
  .lado footer{{display:none}}
}}
</style></head><body>

<div class="app">
  <aside class="lado">
    <div class="marca">
      <span class="av">A</span>
      <div class="nome"><b>Aluguel Certo</b><span>Painel interno</span></div>
    </div>
    <nav>
      <span class="eyebrow">Operação</span>
      <div class="menu">
        <a class="on" href="#"><i></i>Coleta</a>
        <a href="#saude"><i></i>Saúde da esteira</a>
        <a href="#cidades"><i></i>Cidades-alvo</a>
        {'<a href="#plataformas"><i></i>Plataformas</a>' if plataformas else ''}
      </div>
    </nav>
    <footer>
      Roda local, lê o disco.<br>Não vai para a nuvem.
    </footer>
  </aside>

  <main>
    <header class="topo">
      <span class="eyebrow">Painel da coleta</span>
      <h1>Esteira de dados</h1>
      <p>Lido do disco em {agora}.</p>
    </header>

    <section class="kpis">
      {kpi("cidades-alvo", str(len(cidades)), "lista fechada")}
      {kpi("com anúncio", str(coletando), f"de {len(cidades)}")}
      {kpi("à venda", f"{total_v:,}")}
      {kpi("para alugar", f"{total_l:,}")}
      <div class="kpi{' kpi--acento' if problemas else ''}">
        <span class="eyebrow">alertas</span>
        <strong>{len(problemas)}</strong>
        <span class="nota">{particoes} partições lidas</span>
      </div>
    </section>

    <section class="cartao" id="saude">
      <h2>Saúde da esteira</h2>
      <p class="sub">Cada item diz o que está medido e, quando está ruim, o que
      isso causa. Número sem leitura deixa o diagnóstico por conta de quem olha.</p>
      <ul class="saude">{''.join(item_saude(i) for i in itens)}</ul>
    </section>

    <section class="cartao" id="cidades">
      <h2>As {len(cidades)} cidades-alvo</h2>
      <p class="sub">A lista é fechada: coleta acontece nestas e em mais nenhuma.
      Cidade sem anúncio aparece apagada, e não sumida — o que falta coletar é
      informação de operação tanto quanto o que já foi.</p>
      <div class="rolagem"><table>
        <thead><tr>
          <th>cidade</th><th>estado</th><th class="num">venda</th>
          <th class="num">locação</th><th>proporção</th>
          <th class="num">domínios</th><th class="num">última coleta</th>
        </tr></thead>
        <tbody>{''.join(linha_cidade(c) for c in cidades)}</tbody>
      </table></div>
      <div class="legenda">
        <span><i class="b-v"></i>venda</span><span><i class="b-l"></i>locação</span>
      </div>
    </section>

    {f'''<section class="cartao" id="plataformas">
      <h2>Plataformas</h2>
      <p class="sub">O que cada adapter trouxe. Uma partição é um dia de um
      domínio.</p>
      <div class="rolagem"><table>
        <thead><tr><th>plataforma</th><th class="num">domínios</th>
          <th class="num">partições</th><th class="num">linhas</th>
          <th>proporção</th></tr></thead>
        <tbody>{plataformas}</tbody>
      </table></div>
    </section>''' if plataformas else ''}
  </main>
</div>
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
