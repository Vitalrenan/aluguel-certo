"""
Logo Aluguel Certo — vetor de verdade.

Sistema: quatro arcos concentricos formando o C, atravessados pelas duas
pernas do A. Um so centro, um so passo radial, uma so espessura.

O que muda em relacao ao arquivo antigo (PNG dentro de SVG):
  · vetor real: 0,5 KB no lugar de 73 KB, nitido em qualquer tamanho
  · o anel externo deixa de ser cortado pela moldura
  · as pontas dos arcos passam a cair todas no mesmo par de raios
  · espessura unica (antes variava de 52 a 54 px no raster)
  · versao compacta para tamanhos pequenos e versao monocromatica
"""
import math

CENTRO = (58.0, 50.0)
R0, PASSO, TRACO, ABERTURA = 46.0, 10.0, 4.4, 70.0
APICE, BASE = (58.0, 40.0), ((30.0, 92.0), (72.0, 92.0))

def _arco(cx, cy, r, a0, a1):
    p = lambda a: (cx + r*math.cos(math.radians(a)), cy - r*math.sin(math.radians(a)))
    x0, y0 = p(a0); x1, y1 = p(a1)
    grande = 1 if (a0 - a1) % 360 > 180 else 0
    return f"M{x0:.2f} {y0:.2f}A{r:.2f} {r:.2f} 0 {grande} 0 {x1:.2f} {y1:.2f}"

def marca(n_arcos=4, r0=R0, passo=PASSO, traco=TRACO, abertura=ABERTURA,
          apice=APICE, base=BASE, cor="#FFFFFF"):
    cx, cy = CENTRO
    arcos = "".join(f'<path d="{_arco(cx, cy, r0 - k*passo, abertura, -abertura)}"/>'
                    for k in range(n_arcos))
    pernas = "".join(f"M{apice[0]} {apice[1]}L{b[0]} {b[1]}" for b in base)
    return (f'<g fill="none" stroke="{cor}" stroke-width="{traco}" '
            f'stroke-linecap="round" stroke-linejoin="round">{arcos}'
            f'<path d="{pernas}"/></g>')

def envolve(corpo, fundo=None, rx=22):
    caixa = f'<rect width="100" height="100" rx="{rx}" fill="{fundo}"/>' if fundo else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
            'role="img" aria-label="Aluguel Certo"><title>Aluguel Certo</title>'
            f'{caixa}{corpo}</svg>')

VERSOES = {
  # principal — a partir de 28 px
  "logo_AC_vetor.svg":    envolve(marca(), fundo="#2563EB"),
  # compacta — 2 arcos e traco mais grosso, para 16 a 28 px (favicon, avatar)
  "logo_AC_compacto.svg": envolve(marca(n_arcos=2, r0=42, passo=15, traco=6.4,
                                        abertura=74, apice=(58,42),
                                        base=((32,90),(72,90))), fundo="#2563EB"),
  # monocromatica — sem fundo, herda a cor do contexto
  "logo_AC_mono.svg":     envolve(marca(cor="currentColor")),
}
if __name__ == "__main__":
    import pathlib, sys
    sys.stdout.reconfigure(encoding="utf-8")
    for nome, s in VERSOES.items():
        pathlib.Path(nome).write_text(s, encoding="utf-8")
        print(f"  {nome:26} {len(s):>5} bytes")
