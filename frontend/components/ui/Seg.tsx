"use client";

/** Controle segmentado — o `.seg` do artefato. Pílula com o ativo em branco. */
export default function Seg<T extends string>({ opcoes, valor, aoTrocar }: {
  opcoes: { k: T; rot: string }[];
  valor: T;
  aoTrocar: (k: T) => void;
}) {
  return (
    <div className="flex bg-surface-mute rounded-[var(--radius-pill)] p-[3px] gap-0.5 flex-wrap">
      {opcoes.map((o) => (
        <button
          key={o.k}
          type="button"
          aria-pressed={valor === o.k}
          onClick={() => aoTrocar(o.k)}
          className={`px-3.5 py-[5px] rounded-[var(--radius-pill)] text-xs font-medium whitespace-nowrap transition-colors ${
            valor === o.k
              ? "bg-surface text-brand shadow-card"
              : "text-ink-soft hover:text-ink"
          }`}
        >
          {o.rot}
        </button>
      ))}
    </div>
  );
}
