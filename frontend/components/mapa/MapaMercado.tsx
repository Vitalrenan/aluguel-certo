"use client";

import React from "react";

import { GEO } from "@/lib/geo";
import {
  INDICADORES, combina, escala,
  type ChaveIndicador, type LinhaCidade,
} from "@/lib/indicadores";
import {
  ELEV_3D, LIMITES, type Camera, type Vista,
  inv, pathProj, prisma, proj, trava, viewBox,
} from "@/lib/projecao";
import type { Mercado } from "@/lib/api";

/**
 * Mapa de mercado — dois modos, quatro indicadores.
 *
 * Portado do artefato `4027f9a0`. O mapa é FUNDO: lavanda pálida, plano, sem
 * relevo nem textura. O dado são os pinos (modo 2D) ou as barras (modo 3D).
 *
 * Cidade sem dado próprio nem índice aparece com o pino tracejado e entra na
 * contagem do rodapé. Cidade com valor ilustrativo carrega o selo no card
 * flutuante — a marca viaja com o número, não fica num rodapé.
 */

interface Props {
  mercado: Mercado | undefined;
  cidadesComBase: Set<string>;
  indicador: ChaveIndicador;
  visao: "2d" | "3d";
}

const CAM_ZERO: Camera = { z: 1, dx: 0, dy: 0 };
const VISTA_ZERO: Vista = { az: 0, elev: 90 };
const VISTA_3D: Vista = { az: -28, elev: ELEV_3D };

export default function MapaMercado({
  mercado, cidadesComBase, indicador, visao,
}: Props) {
  const [cam, setCam] = React.useState<Camera>(CAM_ZERO);
  const [vista, setVista] = React.useState<Vista>(VISTA_ZERO);
  const [hover, setHover] = React.useState<LinhaCidade | null>(null);
  // A largura entra junto com a posicao: ler `ref.current` durante o render
  // e o que o React proibe -- o valor pode estar velho e a caixa nao
  // reposiciona. No evento o rect ja esta em maos.
  const [pos, setPos] = React.useState<{ x: number; y: number; larg: number } | null>(null);
  const arrasto = React.useRef<{ x: number; y: number; shift: boolean } | null>(null);
  const caixa = React.useRef<HTMLDivElement>(null);

  // Trocar de visao reposiciona a camera. NAO num efeito: `setState` sincrono
  // dentro de efeito dispara render em cascata, e o React aponta isso. Este e
  // o padrao documentado de "ajustar estado quando uma prop muda" -- comparar
  // com o valor anterior durante o render.
  const [visaoAnterior, setVisaoAnterior] = React.useState(visao);
  if (visao !== visaoAnterior) {
    setVisaoAnterior(visao);
    setVista(visao === "3d" ? VISTA_3D : VISTA_ZERO);
    setCam(CAM_ZERO);
  }

  const chaves = React.useMemo(() => Object.keys(GEO.cidades), []);
  const linhas = React.useMemo(
    () => combina(mercado, chaves, cidadesComBase),
    [mercado, chaves, cidadesComBase],
  );

  const def = INDICADORES[indicador];
  // A escala do mapa e a MESMA do trio: calculada sobre todas as cidades, com
  // os quintis do artefato. `esc.cor(v)` devolve a cor da rampa do indicador.
  const esc = escala(linhas.map((l) => l[indicador].valor), def);

  // --- interação: arrastar gira (3D) ou move (2D); ctrl+roda dá zoom -------
  function aoDescer(e: React.PointerEvent) {
    arrasto.current = { x: e.clientX, y: e.clientY, shift: e.shiftKey };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }
  function aoMover(e: React.PointerEvent) {
    const a = arrasto.current;
    if (!a) return;
    const dx = e.clientX - a.x, dy = e.clientY - a.y;
    arrasto.current = { ...a, x: e.clientX, y: e.clientY };
    if (visao === "3d" && !a.shift) {
      setVista((v) => ({
        az: v.az - dx * 0.5,
        elev: trava(v.elev - dy * 0.4, LIMITES.elev),
      }));
    } else {
      setCam((c) => ({ ...c, dx: c.dx - dx / c.z, dy: c.dy - dy / c.z }));
    }
  }
  const aoSubir = () => { arrasto.current = null; };

  function aoRolar(e: React.WheelEvent) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setCam((c) => ({ ...c, z: trava(c.z * (e.deltaY < 0 ? 1.15 : 0.87), LIMITES.z) }));
  }

  const raio = inv(5.5, cam);
  const fonte = inv(8, cam);
  // Acumula as posicoes de rotulo ja usadas neste render.
  const colocados: [number, number][] = [];

  return (
    <div className="relative">
      <div
        ref={caixa}
        onPointerDown={aoDescer}
        onPointerMove={aoMover}
        onPointerUp={aoSubir}
        onPointerLeave={() => { aoSubir(); setHover(null); }}
        onWheel={aoRolar}
        className="relative h-[440px] overflow-hidden rounded-[var(--radius-control)] cursor-grab active:cursor-grabbing bg-surface-mute"
      >
        <svg
          viewBox={viewBox(cam)}
          className="w-full h-full block touch-none"
          role="img"
          aria-label={`Mapa do Brasil — ${def.rot}`}
        >
          {/* o contorno é fundo, não dado */}
          {GEO.brasil.paths.map((d, i) => (
            <path
              key={i}
              d={visao === "3d" ? pathProj(d, vista) : d}
              fill="var(--color-map-fill)"
              stroke="var(--color-map-stroke)"
              strokeWidth={inv(0.6, cam)}
              strokeLinejoin="round"
            />
          ))}

          {/* marcas, ordenadas por profundidade para o 3D não se furar */}
          {linhas
            .map((l) => {
              // POSICAO NO MAPA DO BRASIL, nao o centroide da caixa da cidade.
              // Ver a nota em `lib/geo.ts`: usar `cidades[k].centro` amontoa
              // tudo no meio do pais.
              const p = GEO.brasil.cidades[`${l.nome}|${l.uf}`];
              if (!p) return null;
              const [cx, cy] = p;
              const v = l[indicador].valor;
              return { l, cx, cy, v };
            })
            .filter((x): x is NonNullable<typeof x> => x != null)
            .sort((a, b) => (visao === "3d"
              ? proj(a.cx, a.cy, vista)[1] - proj(b.cx, b.cy, vista)[1]
              : 0))
            .map((m) => {
              // Rotulagem gulosa: o nome so e desenhado se nao colidir com um
              // ja colocado. Sem isto, o Sudeste vira uma pilha ilegivel --
              // Sao Paulo, Rio, Santos, Guaruja e Florianopolis caem quase no
              // mesmo ponto na escala do Brasil. Quem perde o rotulo continua
              // identificavel pelo card flutuante no hover.
              const [px, py] = proj(m.cx, m.cy, vista);
              const perto = colocados.some(
                (c) => Math.abs(c[0] - px) < 34 / cam.z && Math.abs(c[1] - py) < 11 / cam.z,
              );
              if (!perto) colocados.push([px, py]);
              return { ...m, rotulo: !perto };
            })
            .map(({ l, cx, cy, v, rotulo }) => {
              const semDado = v == null;
              const cor = esc ? esc.cor(v) : "#C6D0E6";
              const [px, py] = proj(cx, cy, vista);

              if (visao === "3d" && !semDado) {
                const t = esc ? (v - esc.mn) / (esc.mx - esc.mn || 1) : 0.5;
                const alt = 8 + t * 62;
                return (
                  <g key={`${l.nome}|${l.uf}`}
                     className="cursor-pointer"
                     onPointerEnter={(e) => {
                       setHover(l);
                       const r = caixa.current?.getBoundingClientRect();
                       if (r) setPos({ x: e.clientX - r.left, y: e.clientY - r.top, larg: r.width });
                     }}>
                    {prisma(cx, cy, alt, cor, inv(4.5, cam), vista).map((f, i) => (
                      <polygon key={i} points={f.pts} fill={f.fill} />
                    ))}
                  </g>
                );
              }

              return (
                <g key={`${l.nome}|${l.uf}`}
                   className="cursor-pointer"
                   onPointerEnter={(e) => {
                     setHover(l);
                     const r = caixa.current?.getBoundingClientRect();
                     if (r) setPos({ x: e.clientX - r.left, y: e.clientY - r.top, larg: r.width });
                   }}>
                  <circle cx={px} cy={py} r={raio + inv(2.5, cam)}
                          fill={cor} opacity={semDado ? 0.25 : 0.9} />
                  <circle cx={px} cy={py} r={raio} fill="#fff"
                          stroke={semDado ? "#C3CDE3" : "none"}
                          strokeWidth={inv(1, cam)}
                          strokeDasharray={semDado ? `${inv(2.5, cam)} ${inv(2.5, cam)}` : undefined} />
                  {rotulo && (
                    <text x={px} y={py - raio - inv(4, cam)}
                          fontSize={fonte} textAnchor="middle" fontWeight={600}
                          fill="var(--color-ink-soft)"
                          stroke="var(--color-map-fill)" strokeWidth={inv(2.4, cam)}
                          paintOrder="stroke"
                          className="pointer-events-none select-none">
                      {l.nome}
                    </text>
                  )}
                </g>
              );
            })}
        </svg>

        {/* controles — aparecem no hover */}
        <div className="absolute top-2.5 right-2.5 flex flex-col gap-0.5 bg-surface/90 backdrop-blur rounded-[10px] p-0.5 shadow-card opacity-0 hover:opacity-100 focus-within:opacity-100 transition-opacity">
          {[
            { a: "Aproximar", t: "+", f: () => setCam((c) => ({ ...c, z: trava(c.z * 1.25, LIMITES.z) })) },
            { a: "Afastar", t: "−", f: () => setCam((c) => ({ ...c, z: trava(c.z * 0.8, LIMITES.z) })) },
            { a: "Enquadramento original", t: "⟲", f: () => { setCam(CAM_ZERO); setVista(visao === "3d" ? VISTA_3D : VISTA_ZERO); } },
          ].map((b) => (
            <button key={b.a} title={b.a} aria-label={b.a} onClick={b.f}
                    className="w-[26px] h-[26px] rounded-[7px] grid place-items-center text-sm text-ink-soft hover:bg-brand-soft hover:text-brand transition-colors">
              {b.t}
            </button>
          ))}
        </div>

        <div className="absolute left-2.5 bottom-2 text-[9.5px] text-ink-faint bg-surface/80 px-2 py-0.5 rounded-[var(--radius-pill)] pointer-events-none">
          {visao === "3d" ? "arraste para girar · shift para mover" : "arraste para mover"} · ctrl+rolagem para zoom
        </div>
      </div>

      {/* card flutuante */}
      {hover && pos && (
        <div
          className="absolute z-40 w-[210px] pointer-events-none bg-surface rounded-[14px] shadow-float p-3.5 transition-opacity"
          style={{
            left: Math.min(pos.x + 14, pos.larg - 220),
            top: Math.max(8, pos.y - 90),
          }}
        >
          <div className="flex items-center justify-between gap-2">
            <b className="text-[13px] font-semibold">{hover.nome}</b>
            <span className="text-[10px] text-ink-faint font-semibold">{hover.uf}</span>
          </div>
          <div className="text-[11px] text-ink-soft mb-2.5">
            {hover.base_propria ? "base própria" : "só índice de mercado"}
          </div>
          <hr className="border-0 border-t border-[var(--color-linha)] mb-2.5" />
          <div className="text-[10px] uppercase tracking-[0.06em] text-ink-faint">
            {def.rot}
          </div>
          <div className="text-[15px] font-semibold nums">
            {hover[indicador].valor != null
              ? def.fmt(hover[indicador].valor as number) + def.unid
              : "sem dado"}
          </div>
          {hover[indicador].mock && hover[indicador].valor != null && (
            <div className="mt-2 text-[10px] uppercase tracking-[0.06em] text-ink-faint">
              ● exemplo — ainda não medido
            </div>
          )}
          {!hover[indicador].mock && hover.referencia && (
            <div className="mt-2 text-[10px] text-ink-faint">
              FipeZAP · {hover.referencia}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

