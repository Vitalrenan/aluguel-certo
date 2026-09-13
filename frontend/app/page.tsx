"use client";

import React from "react";
import Link from "next/link";

import Escala from "@/components/mapa/Escala";
import GraficoBarras, { type Barra } from "@/components/mapa/GraficoBarras";
import Ranking, { type ItemRank } from "@/components/mapa/Ranking";
import MapaMercado from "@/components/mapa/MapaMercado";
import Card from "@/components/ui/Card";
import Eyebrow from "@/components/ui/Eyebrow";
import SeloMock from "@/components/ui/SeloMock";
import Seg from "@/components/ui/Seg";
import { useBairros, useMercado, useModelo } from "@/hooks/useDashboardData";
import { GEO } from "@/lib/geo";
import {
  INDICADORES, combina, escala, mesDe, type ChaveIndicador,
} from "@/lib/indicadores";

/**
 * Home — mapa de mercado.
 *
 * Portada do artefato `4027f9a0`, com os dados ligados à API real. O que era
 * embutido lá agora vem de `GET /mercado` e `GET /bairros/indicadores`.
 *
 * DESDE 2026-09-10 SÓ UM NÚMERO DESTA TELA É MOCK: o IPCA por cidade, e por
 * tabela o `val_ipca` que depende dele. Preço, valorização e rentabilidade vêm
 * da FipeZAP. Cidade que a fonte não publica fica de fora do indicador em vez
 * de receber valor de enfeite, e por isso as contagens abaixo de cada card
 * diferem entre si — cada uma é das cidades que têm AQUELE número.
 *
 * Os meses também diferem: preço e valorização vão um mês à frente da
 * rentabilidade. Cada nota cita o mês do campo que ela está mostrando.
 */


export default function Home() {
  const { modelo } = useModelo();
  const { bairros } = useBairros();
  const { mercado } = useMercado();
  const [indicador, setIndicador] = React.useState<ChaveIndicador>("preco");
  const [visao, setVisao] = React.useState<"2d" | "3d">("2d");

  const cidadesComBase = React.useMemo(
    () => new Set(bairros?.resumo.cidades ?? []), [bairros],
  );
  const chaves = React.useMemo(() => Object.keys(GEO.cidades), []);
  const linhas = React.useMemo(
    () => combina(mercado, chaves, cidadesComBase),
    [mercado, chaves, cidadesComBase],
  );

  const def = INDICADORES[indicador];
  const escMapa = escala(linhas.map((l) => l[indicador].valor), def);

  const nMock = linhas.filter((l) => l[indicador].mock && l[indicador].valor != null).length;
  const nSemDado = linhas.filter((l) => l[indicador].valor == null).length;

  // Mês POR CAMPO. A FipeZAP fecha preço e variação um mês antes da
  // rentabilidade; citar um só mês dataria metade da tela errado.
  const mesPreco = mesDe(mercado, "venda_m2");
  const mesRent = mesDe(mercado, "yield_mensal");
  // Contada DENTRO das linhas do mapa. `cidades_sem_rentabilidade` da API e
  // das 57 cidades da tabela FipeZAP inteira; dizer "20 cidades do mapa"
  // quando o mapa tem 15 era falso na propria frase.
  const semRent = linhas.filter(
    (l) => l.preco.valor != null && l.rent.valor == null,
  ).length;


  /**
   * O trio: duas listas de barras e um ranking.
   *
   * A media e sempre das cidades QUE TEM aquele indicador -- a contagem muda
   * entre os tres cards, e a nota abaixo de cada um diz de quantas foi tirada.
   * Media calculada sobre um conjunto e anunciada como outro e o tipo de erro
   * que ninguem percebe olhando a tela.
   */
  const trio = React.useMemo(() => {
    const media = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;

    const monta = (ch: "rent" | "val12") => {
      const comDado = linhas.filter((l) => l[ch].valor != null);
      if (!comDado.length) return { barras: [] as Barra[], media: 0, n: 0, mock: false };
      const vals = comDado.map((l) => l[ch].valor as number);
      // ESCALA SOBRE TODAS as cidades, nao sobre as cinco do topo. Calculada
      // sobre o topo, as cinco barras varreriam a rampa inteira e o grafico
      // viraria arco-iris; sobre o conjunto, as maiores caem juntas na ponta
      // escura -- que e o que a referencia mostra.
      const esc = escala(vals, INDICADORES[ch]);
      const barras: Barra[] = [...comDado]
        .sort((a, b) => (b[ch].valor as number) - (a[ch].valor as number))
        .slice(0, 5)
        .map((l) => ({
          valor: l[ch].valor as number,
          cidade: l.nome, uf: l.uf,
          cor: esc ? esc.cor(l[ch].valor) : "#C6D0E6",
        }));
      return {
        barras, media: media(vals), n: comDado.length,
        mock: comDado.some((l) => l[ch].mock),
      };
    };

    const comPreco = linhas.filter((l) => l.preco.valor != null);
    const ordemPreco = [...comPreco]
      .sort((a, b) => (b.preco.valor as number) - (a.preco.valor as number));
    const precos: ItemRank[] = ordemPreco.slice(0, 5)
      .map((l) => ({ cidade: l.nome, uf: l.uf,
                     valor: l.preco.valor as number, mock: l.preco.mock }));
    const iSantos = ordemPreco.findIndex((l) => l.nome === "Santos");

    return {
      rent: monta("rent"),
      val12: monta("val12"),
      precos,
      nPreco: comPreco.length,
      posSantos: iSantos >= 0 ? iSantos + 1 : null,
      precoSantos: iSantos >= 0 ? (ordemPreco[iSantos].preco.valor as number) : null,
    };
  }, [linhas]);

  return (
    <div className="flex flex-col gap-4 p-6 max-w-[1400px] w-full mx-auto">
      {/* ── mapa ───────────────────────────────────────────────────────── */}
      <Card
        titulo="Mercado no Brasil"
        sub={`${linhas.length} cidades · ${cidadesComBase.size} com base própria`}
        acao={
          <div className="flex items-center gap-2.5 flex-wrap justify-end">
            <Seg
              valor={visao}
              aoTrocar={setVisao}
              opcoes={[{ k: "2d", rot: "Mapa" }, { k: "3d", rot: "Barras 3D" }]}
            />
          </div>
        }
      >
        <div className="flex items-end justify-between gap-6 flex-wrap mb-4">
          <Seg
            valor={indicador}
            aoTrocar={setIndicador}
            opcoes={[
              { k: "preco", rot: "Preço do m²" },
              { k: "val12", rot: "Valorização anual" },
              { k: "val_ipca", rot: "Valorização ÷ inflação" },
              { k: "rent", rot: "Rentabilidade" },
            ]}
          />
          <Escala esc={escMapa} />
        </div>

        <MapaMercado
          mercado={mercado}
          cidadesComBase={cidadesComBase}
          indicador={indicador}
          visao={visao}
        />

        <p className="mt-3.5 text-[11.5px] text-ink-faint leading-relaxed">
          {def.mock && nMock > 0 && (
            <>
              <b className="text-ink-soft font-semibold">{nMock} das {linhas.length} cidades</b>{" "}
              exibem valor ilustrativo para {def.rot.toLowerCase()}: {def.mock}.{" "}
            </>
          )}
          {nSemDado > 0 && (
            <>{nSemDado} sem esse dado, com o pino tracejado. </>
          )}
          {mercado && (
            <>
              Fonte: Índice FipeZAP — preço e valorização de {mesPreco},
              rentabilidade de {mesRent}.
            </>
          )}
        </p>
      </Card>

      {/* ── trio ───────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card titulo="Maiores rentabilidades" sub="aluguel ÷ valor de mercado · ao ano"
              acao={trio.rent.mock ? <SeloMock /> : undefined}>
          <GraficoBarras itens={trio.rent.barras} media={trio.rent.media} ind={INDICADORES.rent} />
          <p className="mt-3.5 text-[11.5px] leading-relaxed text-ink-faint">
            A linha tracejada é a média das{" "}
            <b className="text-ink-soft font-semibold">{trio.rent.n} cidades</b>{" "}
            acompanhadas: <b className="text-ink-soft font-semibold">
              {trio.rent.n ? INDICADORES.rent.fmt(trio.rent.media) : "—"}
            </b> ao ano.
            {mercado && <> Fonte: Índice FipeZAP, {mesRent}.</>}
            {semRent > 0 && (
              <> Outras {semRent} do mapa entram só com preço: a FipeZAP não
                 publica a rentabilidade delas.</>
            )}
          </p>
        </Card>

        <Card
          titulo="Maiores valorizações"
          sub="variação do m² em 12 meses"
          acao={trio.val12.mock ? <SeloMock /> : undefined}
        >
          <GraficoBarras itens={trio.val12.barras} media={trio.val12.media} ind={INDICADORES.val12} />
          <p className="mt-3.5 text-[11.5px] leading-relaxed text-ink-faint">
            Média das <b className="text-ink-soft font-semibold">{trio.val12.n} cidades</b>:{" "}
            <b className="text-ink-soft font-semibold">
              {trio.val12.n ? INDICADORES.val12.fmt(trio.val12.media) : "—"}
            </b>. Quem ficou abaixo da linha valorizou menos que o conjunto.
            {mercado && (
              <> Fonte: Índice FipeZAP, variação do m² nos 12 meses até {mesPreco}.</>
            )}
          </p>
        </Card>

        <Card titulo="Metro quadrado mais caro" sub="preço médio de venda residencial">
          <Ranking itens={trio.precos} indicador="preco" />
          <p className="mt-3.5 text-[11.5px] leading-relaxed text-ink-faint">
            Entre as <b className="text-ink-soft font-semibold">{trio.nPreco} cidades</b>{" "}
            acompanhadas.
            {mercado && <> Índice FipeZAP, {mesPreco}.</>}
            {trio.posSantos && trio.precoSantos != null && (
              <> Santos aparece em {trio.posSantos}º, com{" "}
                <b className="text-ink-soft font-semibold">
                  {INDICADORES.preco.fmt(trio.precoSantos)}
                </b>.</>
            )}
          </p>
        </Card>
      </div>

      {/* ── faixa de destaque ──────────────────────────────────────────── */}
      <section className="rounded-[var(--radius-card)] shadow-card text-white grid md:grid-cols-[1.55fr_1fr] gap-9 p-8 relative overflow-hidden"
               style={{ background: "linear-gradient(118deg, var(--color-brand-escuro) 0%, var(--color-brand) 52%, #3B82F6 100%)" }}>
        <span aria-hidden className="absolute -right-[70px] -top-[90px] w-[300px] h-[300px] rounded-full bg-white/[0.07]" />
        <div className="relative z-10">
          <Eyebrow className="!text-white/60">Calculadora com o modelo real</Eyebrow>
          <h2 className="text-[27px] font-medium tracking-tight mt-2 max-w-[19ch] leading-tight">
            Você está pagando o preço certo?
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-white/[0.84] max-w-[52ch]">
            O modelo aprendeu com{" "}
            <b className="text-white font-semibold">
              {modelo ? modelo.n_treino.toLocaleString("pt-BR") : "milhares de"} imóveis
            </b>{" "}
            anunciados. Descreva o seu e receba a faixa de valor, com o erro que
            medimos — não um número solto.
          </p>
          <div className="flex gap-5 flex-wrap my-5">
            {["Descreva o imóvel", "O modelo compara", "Receba a faixa"].map((t, i) => (
              <span key={t} className="flex items-center gap-2 text-[12.5px] text-white/[0.84]">
                <b className="w-[21px] h-[21px] rounded-full bg-white/[0.18] grid place-items-center text-[11px] font-bold">
                  {i + 1}
                </b>
                {t}
              </span>
            ))}
          </div>
          <Link href="/calculadora"
                className="inline-block bg-white text-brand rounded-[var(--radius-control)] px-[22px] py-[13px] font-semibold hover:-translate-y-px transition-transform">
            Calcular meu imóvel →
          </Link>
        </div>
        <ul className="list-none m-0 p-0 md:pl-[30px] md:border-l border-white/[0.18] flex flex-col justify-center gap-[22px] relative z-10">
          <li className="flex gap-3 items-start">
            <span className="text-sm leading-relaxed text-white/[0.88]">
              Descubra se o valor está <b className="text-white font-semibold">acima ou abaixo</b> do mercado do bairro
            </span>
          </li>
          <li className="flex gap-3 items-start">
            <span className="text-sm leading-relaxed text-white/[0.88]">
              Receba também o <b className="text-white font-semibold">aluguel de referência</b>, pela rentabilidade da cidade
            </span>
          </li>
        </ul>
      </section>
    </div>
  );
}
