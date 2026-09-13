/**
 * `<SeloMock>` — design-system.md §5 e §10.
 *
 * A regra do projeto é que dado inventado nunca sai com a mesma cara de dado
 * medido. Este selo é o que torna o mock aceitável: ele aparece NO CARD, junto
 * do número, não num rodapé de documentação que ninguém lê.
 *
 * O checklist do §10 tem o item "todo card com dado mockado mostra
 * `<SeloMock>`". Se um dia o dado virar real, o selo some porque o campo
 * `mock` da fonte vira `false` — não porque alguém lembrou de apagar a marca.
 */
export default function SeloMock({ motivo }: { motivo?: string }) {
  return (
    <span
      title={motivo ?? "valor ilustrativo, ainda não medido"}
      className="inline-flex items-center gap-1.5 h-[22px] px-2.5 rounded-[var(--radius-pill)] bg-surface-mute text-ink-faint text-[11px] font-semibold uppercase tracking-[0.06em] whitespace-nowrap"
    >
      <span aria-hidden className="w-1.5 h-1.5 rounded-full border border-current" />
      exemplo
    </span>
  );
}
