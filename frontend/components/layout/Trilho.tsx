"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Trilho lateral — a casca do artefato `4027f9a0`.
 *
 * 212px fixos, grudado no topo, com a marca, três seções e o rodapé. Abaixo de
 * 860px vira barra horizontal rolável, como no artefato.
 *
 * Substitui o `Header` horizontal. O `aria-current="page"` é o que pinta o item
 * ativo — nada de estado em JS para isso.
 */

const ITENS = [
  {
    href: "/", rot: "Mapa de mercado",
    d: "M9 20 3 17V4l6 3m0 13 6-3m-6 3V7m6 10 6 3V7l-6-3m0 13V4",
  },
  {
    href: "/calculadora", rot: "Calculadora",
    d: "M8 6h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15v4",
    caixa: true,
  },
  {
    href: "/sobre", rot: "Sobre nós",
    d: "M12 16v-4m0-4h.01", circulo: true,
  },
];

export default function Trilho() {
  const caminho = usePathname();
  return (
    <aside className="bg-surface border-r border-[var(--color-linha)] flex flex-col gap-7 px-4 pt-6 pb-5 sticky top-0 h-screen w-[212px] shrink-0
                      max-[860px]:flex-row max-[860px]:items-center max-[860px]:h-auto max-[860px]:static max-[860px]:w-full
                      max-[860px]:border-r-0 max-[860px]:border-b max-[860px]:py-3 max-[860px]:gap-3.5 max-[860px]:overflow-x-auto">
      <Link href="/" className="flex items-center gap-3 px-1 shrink-0">
        <span className="w-[42px] h-[42px] rounded-xl grid place-items-center text-white font-bold text-lg shrink-0"
              style={{ background: "var(--color-brand)", boxShadow: "0 1px 4px rgb(21 32 59/.18)" }}>
          A
        </span>
        <span className="max-[860px]:hidden">
          <b className="block text-[14.5px] font-semibold tracking-tight leading-tight">Aluguel Certo</b>
          <span className="block text-[10px] text-ink-faint font-medium tracking-[0.04em] uppercase">
            Inteligência de mercado
          </span>
        </span>
      </Link>

      <nav className="flex flex-col gap-[3px] max-[860px]:flex-row max-[860px]:gap-1">
        {ITENS.map((i) => {
          const ativo = caminho === i.href;
          return (
            <Link key={i.href} href={i.href}
                  aria-current={ativo ? "page" : undefined}
                  className={`flex items-center gap-[11px] px-3 py-2.5 rounded-[var(--radius-control)] text-[13.5px] font-medium transition-colors whitespace-nowrap ${
                    ativo ? "bg-brand-soft text-brand font-semibold"
                          : "text-ink-soft hover:bg-surface-mute hover:text-ink"
                  }`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                   strokeLinecap="round" strokeLinejoin="round" className="w-[18px] h-[18px] shrink-0">
                {i.caixa && <rect x="4" y="2" width="16" height="20" rx="2" />}
                {i.circulo && <circle cx="12" cy="12" r="9" />}
                <path d={i.d} />
              </svg>
              {i.rot}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto text-[11px] text-ink-faint px-2 leading-relaxed max-[860px]:hidden">
        Aluguel Certo · 2026<br />Dados de mercado e estimativa por modelo
      </div>
    </aside>
  );
}
