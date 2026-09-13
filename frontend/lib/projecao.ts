/**
 * Projeção isométrica do mapa 3D.
 *
 * Portado do artefato `4027f9a0` (06/09/2026). São vinte linhas de matemática,
 * não uma biblioteca — girar o mapa é rotacionar no plano e achatar em Y pelo
 * seno da elevação.
 *
 * O centro de rotação é (150,150) porque toda a geometria do `lib/geo.ts` vive
 * numa viewBox de 300×300 por cidade.
 */

export const CENTRO = 150;
export const ELEV_3D = 34;
export const LIMITES = { z: [1, 8] as [number, number],
                         elev: [14, 82] as [number, number] };

export interface Vista { az: number; elev: number }
export interface Camera { z: number; dx: number; dy: number }

export const trava = (v: number, [a, b]: [number, number]) =>
  Math.max(a, Math.min(b, v));

/** (x,y) do plano -> (x,y) da tela, girado por `az` e achatado por `elev`. */
export function proj(x: number, y: number, vista: Vista): [number, number] {
  const a = (vista.az * Math.PI) / 180;
  const e = Math.sin((vista.elev * Math.PI) / 180);
  const px = x - CENTRO, py = y - CENTRO;
  const c = Math.cos(a), s = Math.sin(a);
  return [CENTRO + (px * c - py * s), CENTRO + (px * s + py * c) * e];
}

/**
 * Profundidade de um ponto na direção da câmera.
 *
 * É o que ordena as faces do prisma: com o giro, qualquer uma das quatro pode
 * vir para a frente, e desenhar na ordem fixa produziria um sólido furado.
 */
export function prof(x: number, y: number, vista: Vista): number {
  const a = (vista.az * Math.PI) / 180;
  return (x - CENTRO) * Math.sin(a) + (y - CENTRO) * Math.cos(a);
}

/** Reprojeta cada par `x,y` de um path SVG, subindo `dz` pixels. */
export function pathProj(d: string, vista: Vista, dz = 0): string {
  return d.replace(
    /(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g,
    (_, a: string, b: string) => {
      const [X, Y] = proj(+a, +b, vista);
      return `${X.toFixed(1)},${(Y - dz).toFixed(1)}`;
    },
  );
}

export const viewBox = (cam: Camera) => {
  const l = 300 / cam.z;
  return `${(CENTRO - l / 2 + cam.dx).toFixed(2)} ${(CENTRO - l / 2 + cam.dy).toFixed(2)} ${l.toFixed(2)} ${l.toFixed(2)}`;
};

/** Tamanho constante na tela, independente do zoom. */
export const inv = (v: number, cam: Camera) => +(v / cam.z).toFixed(2);

function escurece(hex: string, f: number): string {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * f);
  const g = Math.round(((n >> 8) & 255) * f);
  const b = Math.round((n & 255) * f);
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

export interface Face { pts: string; fill: string; topo?: boolean }

/**
 * As cinco faces de uma barra: quatro laterais ordenadas por profundidade,
 * mais o topo. Os tons `[.66,.80,.72,.86]` dão o sombreamento sem luz calculada.
 */
export function prisma(
  x: number, y: number, alt: number, cor: string, s: number, vista: Vista,
): Face[] {
  const cantos: [number, number][] = [
    [x - s, y - s], [x + s, y - s], [x + s, y + s], [x - s, y + s],
  ];
  const P = cantos.map((c) => proj(c[0], c[1], vista));
  const pol = (ps: number[][]) =>
    ps.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");

  const tons = [0.66, 0.8, 0.72, 0.86];
  const faces = [0, 1, 2, 3]
    .map((k) => {
      const a = cantos[k], b = cantos[(k + 1) % 4];
      const A = P[k], B = P[(k + 1) % 4];
      return {
        d: prof(a[0], a[1], vista) + prof(b[0], b[1], vista),
        pts: pol([A, B, [B[0], B[1] - alt], [A[0], A[1] - alt]]),
        fill: escurece(cor, tons[k]),
      };
    })
    .sort((u, v) => u.d - v.d)
    .map(({ pts, fill }) => ({ pts, fill }));

  return [...faces, { pts: pol(P.map((p) => [p[0], p[1] - alt])), fill: cor, topo: true }];
}

// A rampa NAO vive aqui. Cada indicador tem a sua, em `lib/indicadores.ts`, e
// a cor de cada marca vem de `escala.cor(v)`. Uma rampa unica neste modulo foi
// o erro da primeira versao: apagava a distincao entre os quatro indicadores,
// que na referencia sao azul, ciano, divergente e azul-suave.
