# Design system — especificação executável

**2026-09-05 · v0.1** · complementa [plano-ux-wireframes.md](plano-ux-wireframes.md)

O plano decide *o que* a tela mostra. Este documento decide *como se constrói*:
tokens, CSS, anatomia de componente e as armadilhas de implementação.

Stack medida no `package.json`: Next 16.3.4 · React 19.2.8 · **Tailwind 4.3.3**.
Tailwind 4 significa **tema no CSS** (`@theme`), não em `tailwind.config.js` — o
arquivo de config não existe neste projeto e não deve ser criado.

---

## 1. Leitura da referência

Antes dos tokens, o que a referência de fato faz. Reproduzir a paleta sem
reproduzir estas sete decisões produz outra coisa.

```
  1  Cartao branco sobre cinza-gelo. Separacao por SOMBRA DIFUSA, nunca borda.
  2  Raio grande e uniforme (~16-20px). Nada de raio misto na mesma tela.
  3  Mapa PLANO em lavanda palida. Sem relevo, sem 3D, sem textura.
     O mapa e fundo; o dado sao os pinos.
  4  Pilula de contagem colada ao titulo da regiao.
  5  Legenda de pontos no canto superior direito, minusculas, 12px, discreta.
  6  UM card de acento em menta. Um so. E o unico bloco de cor cheia da tela.
  7  Link de rodape centralizado por painel + setas de paginacao no canto.
```

**A decisão mais importante é a que não se vê:** a referência é
majoritariamente branca e silenciosa. A cor aparece em quatro lugares — pinos,
uma pílula, o card menta, o anel de progresso. **Saturar o resto destrói o
efeito.** Se na revisão a tela parecer colorida, ela está errada.

---

## 2. Tokens

Vão em `app/globals.css`, dentro de `@theme`. **Não** criar um segundo arquivo de
tokens: `styles/design-tokens.css` existe hoje, está órfão, tem o `:root`
triplicado e um bloco sem seletor. Ele deve ser **apagado**, não importado.

```css
@import "tailwindcss";
@import "leaflet/dist/leaflet.css";

@theme {
  /* superficies */
  --color-canvas:        #F1F3F7;   /* fundo da pagina */
  --color-surface:       #FFFFFF;   /* card */
  --color-surface-mute:  #F8F9FB;   /* linha zebrada, hover de tabela */

  /* mapa */
  --color-map-fill:      #DDE0F5;
  --color-map-stroke:    #C7CBEA;
  --color-map-hover:     #CDD2F0;

  /* texto */
  --color-ink:           #1F2937;
  --color-ink-soft:      #6B7280;
  --color-ink-faint:     #9CA3AF;

  /* acentos */
  --color-brand:         #4F46E5;
  --color-brand-soft:    #EEF0FE;   /* fundo de item ativo na sidebar */
  --color-mint:          #CFF3EA;   /* card de acento */
  --color-mint-ink:      #0F3D33;

  /* faixas de preco — percentil do recorte, ver plano §1 */
  --color-faixa-abaixo:  #10B981;
  --color-faixa-media:   #4F46E5;
  --color-faixa-acima:   #F59E0B;

  /* resultado da calculadora */
  --color-res-oportuno:  #10B981;
  --color-res-justo:     #4F46E5;
  --color-res-atencao:   #F59E0B;
  --color-res-alerta:    #EF4444;

  /* forma */
  --radius-card:         1.25rem;   /* 20px */
  --radius-control:      0.75rem;   /* 12px */
  --radius-pill:         9999px;

  /* tipografia */
  --font-sans:           var(--font-inter), ui-sans-serif, system-ui, sans-serif;
}
```

### Sombras

Tailwind 4 não tokeniza sombra da mesma forma; declarar como utilitários:

```css
@layer utilities {
  .shadow-card {
    box-shadow: 0 1px 2px rgb(17 24 39 / 0.04),
                0 8px 24px -8px rgb(17 24 39 / 0.08);
  }
  .shadow-float {
    box-shadow: 0 2px 4px rgb(17 24 39 / 0.06),
                0 16px 40px -12px rgb(17 24 39 / 0.16);
  }
}
```

`shadow-card` é o repouso. `shadow-float` é o card flutuante do pino e o hover.
**Duas sombras, não cinco** — a referência tem exatamente dois níveis de
elevação, e é isso que faz a tela parecer calma.

> **Nomes de token não podem colidir com utilitários do Tailwind.** O arquivo
> órfão atual redefine `.text-gray-500` e `.text-gray-900`, que o Tailwind já
> gera — colisão de cascata que muda a cor em toda a aplicação sem erro. Por isso
> os nomes acima são `ink`, `canvas`, `surface`, e não `gray-*`.

---

## 3. Escala tipográfica

Inter via `next/font/google` — self-hosted, sem requisição externa em runtime.

| papel | classe | uso |
|---|---|---|
| número herói | `text-5xl font-semibold tracking-tight` | o `11.344` do card menta |
| número de card | `text-3xl font-semibold tracking-tight` | KPI |
| título de card | `text-lg font-medium` | "Imóveis no mapa" |
| subtítulo | `text-sm text-ink-soft` | linha sob o título |
| eyebrow | `text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint` | "MENU", "BAIRRO" |
| corpo | `text-sm` | — |
| tabela | `text-sm tabular-nums` | **`tabular-nums` obrigatório** |
| legenda | `text-xs text-ink-soft` | legenda de pontos |

`font-variant-numeric: tabular-nums` em toda coluna de número. Sem isso as
colunas de preço da tabela dançam entre linhas.

---

## 4. Grade e espaçamento

```
  sidebar          80px (so icones)  ·  240px (expandida)
  gutter da pagina 24px
  gap entre cards  16px
  padding de card  24px  (28px no card do mapa)
  altura do mapa   320px
```

A home é grid de 12 colunas:

```
  ┌── sidebar ──┬───────────────── conteudo ─────────────────┐
  │             │  painel de mapa .................. span 12 │
  │             │  bairros (5) · confianca (4) · menta (3)   │
  │             │  CTA (8) ................ anuncio (4)      │
  │             │  tabela .......................... span 12 │
  └─────────────┴────────────────────────────────────────────┘
```

Usar `grid` + `gap`, nunca margem por elemento — margem colapsa e dobra de
formas difíceis de rastrear.

---

## 5. Anatomia dos componentes

### `<Card>`

```tsx
<section className="bg-surface rounded-[--radius-card] shadow-card p-6">
  <header className="flex items-start justify-between gap-4 mb-5">
    <div>
      <h2 className="text-lg font-medium text-ink">{titulo}</h2>
      {sub && <p className="text-sm text-ink-soft mt-0.5">{sub}</p>}
    </div>
    {acao}            {/* legenda, menu "...", ou nada */}
  </header>
  {children}
</section>
```

Server Component. **Não marcar `"use client"`** — o `Card.tsx` atual está marcado
sem usar hook nem evento, e arrasta a subárvore para o bundle do cliente.

### `<CardAcento>` — o menta

Único bloco de cor cheia. Fundo `--color-mint`, texto `--color-mint-ink`,
número herói, ícone discreto no rodapé, menu `···` no canto.

**Um por tela.** Dois cards menta e o efeito acaba.

### `<Pilula>`

```
  altura 22px · px-2.5 · text-xs font-medium · rounded-pill
```

Duas variantes:
- **contagem** — `bg-brand-soft text-brand` ("412 imóveis")
- **faixa** — ponto colorido 6px + rótulo, `bg-surface-mute text-ink-soft`

A variante de faixa **sempre carrega o rótulo textual**, nunca só a cor
(plano §1, acessibilidade).

### `<PainelCidade>`

```
  ┌─────────────────────────────────┐
  │ Santos            ⟨412 imoveis⟩ │   titulo + pilula
  │ ┌─────────────────────────────┐ │
  │ │        [ MapaCidade ]       │ │   320px
  │ └─────────────────────────────┘ │
  │          Ver bairros            │   link centralizado
  └─────────────────────────────────┘
```

Três painéis lado a lado em ≥1280, separados por divisória vertical de 1px em
`--color-map-stroke` a 40% — é o que a referência usa entre os países.

### `<MapaCidade>`

**Decisão: Leaflet, não SVG estático.** `react-leaflet` já está instalado e o
`PriceMap.tsx` já monta um `MapContainer`. O que muda:

1. **GeoJSON local em `public/geo/`**, não `raw.githubusercontent.com`. O
   componente atual depende do GitHub estar no ar.
2. **Preenchimento `--color-map-fill`, contorno `--color-map-stroke`** — plano,
   sem tiles fotográficos. `<TileLayer>` fica **fora**: a referência não tem base
   cartográfica, e é isso que deixa o mapa silencioso.
3. **Um marcador por bairro**, não por imóvel. O MVP fazia laço `iloc` sobre
   cada linha e travava. Cluster acima de ~500.
4. **Interação desligada por padrão** — `zoomControl={false}`,
   `scrollWheelZoom={false}`, `dragging={false}`. O mapa é uma figura, não um
   mapa navegável; scroll dentro dele sequestrando a rolagem da página é o
   defeito clássico.

O pino: círculo de 10px, `border: 3px solid var(--faixa)`, miolo branco, sombra
suave. Hover eleva para `shadow-float` e abre o `<CardImovel>`.

### `<CardImovel>` — o flutuante do pino

Reproduz o card branco da referência com o canto dobrado. Largura ~200px,
`shadow-float`, ancorado ao pino com deslocamento, `z-index` acima do mapa.

```
  ◆  Gonzaga
     Santos - SP
     ──────────────
     Preco medio
     R$ 6.240 /m²
```

**Nunca deixar sair da viewport.** Perto da borda direita, o card vira para a
esquerda do pino.

### `<LegendaFaixas>`

Canto superior direito do card do mapa, `text-xs`:

```
  ● abaixo do mercado   ● na media   ● acima do mercado
```

Ponto de 6px + rótulo. Clicável: alterna a visibilidade daquela faixa no mapa —
a referência não faz isso, mas é grátis e transforma legenda em filtro.

### `<AnelConfianca>`

O donut de 52% da referência. `stroke-linecap: round`, trilha em
`--color-surface-mute`, progresso em `--color-brand`, número no centro
(`text-3xl`), rótulo abaixo em `text-xs`.

SVG puro. **Não usar recharts nem plotly para isso** — é um arco, e as duas
bibliotecas juntas hoje pesam megabytes. Ver §8.

### `<TabelaBairros>`

Cabeçalho em eyebrow. Linhas de 48px, hover `--color-surface-mute`, sem borda
entre linhas (a referência separa por espaço, não por régua). Penúltima coluna é
a pílula de faixa; última é o `···`.

Números com `tabular-nums` e alinhados à direita. Texto à esquerda.

`<600px` a tabela vira lista de cartões — **nunca scroll horizontal**.

### `<Sidebar>`

80px, ícones `lucide-react` de 20px, grupos separados por eyebrow. Item ativo:
`bg-brand-soft text-brand rounded-control`. Avatar no topo com anel de status.

Correções sobre o `Sidebar.tsx` atual:
- `href="#"` em todos os itens → rotas reais, ou `<button>` se não navega.
- Avatar de `i.pravatar.cc` via `<img>` cru → `next/image` com asset local.
  Host externo em produção, sem `width`/`height`, causa layout shift e o
  `core-web-vitals` acusa.

### `<SeloMock>`

Canto superior direito do card, ao lado do menu. `text-[10px]`, uppercase,
`text-ink-faint`, fundo `--color-surface-mute`, `rounded-pill`.

```
  ┌─────────────────── ⟨ dados de exemplo ⟩ ┐
```

Discreto de propósito: precisa ser legível sem virar aviso de erro. Lê `origem`
do próprio dado (plano §0.1) — o componente não decide.

---

## 6. Tema claro e escuro

A referência é clara. **O escuro não é opcional** — o `globals.css` já tem
`.dark body` e o `Header` já tem o toggle, então hoje existe um botão que não
pinta nada, porque `--color-bg-dark` só está definido no arquivo órfão.

Duas saídas honestas:

**A — assumir o modo claro** (recomendado para a v1): remover a regra `.dark`, o
`ThemeProvider` e o botão. A referência é uma tela clara e clínica; entregar um
escuro mal calibrado é pior que não ter.

**B — fazer os dois:** redefinir **só os tokens** sob
`@media (prefers-color-scheme: dark)` guardado por `:root:not([data-theme="light"])`
e sob `:root[data-theme="dark"]`. Nenhuma cor pode ter sua única definição dentro
de um bloco de tema.

**Não** deixar como está: um toggle que não faz nada é pior que ausência.

Se for a B, o `ThemeProvider` precisa sair de cima do `<html>` (hoje envolve o
elemento raiz, o que é inválido no App Router) e o `<html>` precisa de
`suppressHydrationWarning`.

---

## 7. Movimento

```
  hover de card       shadow-card → shadow-float     180ms ease-out
  entrada de card     opacity 0→1, translateY 8px→0  240ms, stagger 40ms
  pino                scale 1 → 1.15                 120ms
  troca de filtro     crossfade do conteudo          200ms
```

Sem parallax, sem scroll-jacking, sem contador animado em número medido — número
que sobe na tela sugere tempo real onde não há.

`prefers-reduced-motion: reduce` desliga tudo acima e mantém só o crossfade.

---

## 8. Bibliotecas — o que fica

O `package.json` traz hoje **três** stacks de visualização. Não é sustentável:
`plotly.js` sozinho é da ordem de megabytes, e recharts cobre o mesmo terreno.

| biblioteca | veredicto |
|---|---|
| `leaflet` + `react-leaflet` | **fica** — é o mapa |
| `recharts` | **fica** — barras e linhas |
| `plotly.js` + `react-plotly.js` | **sai** — nada nesta especificação precisa dele, e é o mais pesado |
| `d3-scale` | **fica** — escala de percentil das faixas |
| `d3-scale-chromatic` | **sai** — importado e nunca usado; e falta o `@types` |
| `swr` | **fica** — camada de fetch |
| `next-themes` | depende da §6: fica em B, sai em A |

O anel de confiança e o box plot são **SVG escrito à mão**. Ambos são geometria
simples e específica, e nenhuma biblioteca desenha o box plot da §7.1 do plano
sem contorção — ele recebe percentis prontos, não uma amostra.

---

## 9. Ordem de construção

Nada de UI nova antes dos dois primeiros. Construir sobre build quebrado é
acumular defeito não verificável.

```
  0  Zerar os 3 erros de tsc                      → ARQUITETURA-FRONTEND.md §8
  1  Tokens em globals.css + apagar design-tokens.css orfao
     Resolver a §6 (tema claro assumido ou os dois feitos)
  ─────────────────────────────────────────────────────────
  2  <Card> <CardAcento> <Pilula> <Eyebrow> <SeloMock>
  3  lib/mock/*.ts com os tipos do plano §10.1
  4  <MapaCidade> + <PainelCidade> + <CardImovel> + <LegendaFaixas>
  5  <BarraBairros> <AnelConfianca> <TabelaBairros>
  6  Sidebar corrigida + layout da home
  7  Filtros (quartos, area, bairro) governando a pagina inteira
  8  Estados: carregando, vazio, erro, filtro-sem-resultado
  ─────────────────────────────────────────────────────────
  9  Calculadora, modal, relatorio
```

O passo 8 não é polimento. Os quatro estados são a diferença entre uma tela que
funciona na demonstração e uma que funciona com dado real — e o de
"filtro sem resultado" é o mais provável de acontecer com um usuário de verdade.

---

## 10. Checklist de revisão visual

Antes de considerar uma tela pronta:

- [ ] A tela parece **branca e silenciosa**? Cor só nos pinos, numa pílula, no
      card menta e no anel?
- [ ] **Um** card de acento, não dois?
- [ ] Todo raio é `--radius-card` ou `--radius-control`? Nenhum valor solto?
- [ ] **Duas** sombras apenas?
- [ ] Toda coluna de número tem `tabular-nums`?
- [ ] Toda cor de faixa vem acompanhada de rótulo textual?
- [ ] Nenhuma cor definida **só** dentro de bloco de tema?
- [ ] Nenhum literal numérico dentro de componente — tudo vem de `lib/mock`?
- [ ] Todo card com dado mockado mostra `<SeloMock>`?
- [ ] Os quatro estados existem em cada painel de dados?
- [ ] `<600px`: tabela virou cartões, formulário é uma coluna?
- [ ] Foco de teclado visível; modal prende foco e fecha no `Esc`?
