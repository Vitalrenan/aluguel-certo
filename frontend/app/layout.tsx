import type { Metadata } from "next";
import { Inter } from "next/font/google";

import Trilho from "@/components/layout/Trilho";
import "./globals.css";

/**
 * MODO CLARO ASSUMIDO — design-system.md §6, opção A.
 *
 * O `ThemeProvider` do `next-themes` saiu, e com ele o toggle do `Header`. Três
 * motivos, o primeiro deles um defeito real:
 *
 *   1. o provider envolvia o `<html>`, o que é inválido no App Router;
 *   2. a regra `.dark body` apontava para `--color-bg-dark`, definida apenas no
 *      `styles/design-tokens.css`, que nunca foi importado. **O botão nunca
 *      pintou nada** -- era um controle morto desde o primeiro dia;
 *   3. a referência do design é uma tela clara e clínica. A §6 é explícita:
 *      entregar um escuro mal calibrado é pior que não ter, e deixar um toggle
 *      que não faz nada é pior que os dois.
 *
 * Para fazer o escuro de verdade (opção B), a regra é redefinir SÓ os tokens
 * sob `@media (prefers-color-scheme: dark)` e `:root[data-theme="dark"]` --
 * nenhuma cor pode ter sua única definição dentro de um bloco de tema.
 */

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Aluguel Certo",
  description:
    "Estimativa de valor de imóvel a partir de anúncios reais, com a margem de erro medida.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" className={`${inter.variable} h-full`}>
      <body className="min-h-full">
        {/* Casca do artefato: trilho fixo + area de conteudo. Abaixo de 860px
            o grid colapsa e o trilho vira barra horizontal. */}
        <div className="grid grid-cols-[212px_1fr] min-h-screen max-[860px]:grid-cols-1">
          <Trilho />
          <main className="min-w-0">{children}</main>
        </div>
      </body>
    </html>
  );
}
