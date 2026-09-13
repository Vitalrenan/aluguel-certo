import { INDICADORES, type ChaveIndicador } from "@/lib/indicadores";
import SeloMock from "@/components/ui/SeloMock";

/**
 * Lista ordenada com trilho de proporção — o `.rank` do artefato.
 *
 * O trilho é a barra fina à direita do nome: largura proporcional ao maior da
 * lista. Ele existe para o olho comparar sem ler todos os números.
 */
export interface ItemRank {
  cidade: string;
  uf: string;
  valor: number;
  mock: boolean;
}

export default function Ranking({ itens, indicador }: {
  itens: ItemRank[];
  indicador: ChaveIndicador;
}) {
  const def = INDICADORES[indicador];
  const maior = itens.length ? Math.max(...itens.map((i) => i.valor)) : 1;
  return (
    <ol className="list-none m-0 mt-1 p-0 flex flex-col">
      {itens.map((it, k) => (
        <li key={`${it.cidade}|${it.uf}`}
            className="flex items-center gap-2.5 py-[9px] border-t border-[var(--color-linha)] first:border-t-0">
          <span className="w-[18px] text-[11px] font-bold text-ink-faint nums shrink-0">
            {k + 1}
          </span>
          <span className="flex-1 text-[13px] font-medium min-w-0 truncate">
            {it.cidade}
            <i className="not-italic text-[10.5px] text-ink-faint ml-1.5 font-normal">
              {it.uf}
            </i>
          </span>
          {it.mock && <SeloMock />}
          <span className="w-[52px] h-[5px] rounded-[var(--radius-pill)] bg-surface-mute shrink-0 overflow-hidden">
            <i className="block h-full rounded-[var(--radius-pill)] bg-brand"
               style={{ width: `${((it.valor / maior) * 100).toFixed(1)}%` }} />
          </span>
          <span className="w-[86px] text-right text-[12.5px] font-semibold nums shrink-0">
            {def.fmt(it.valor)}
          </span>
        </li>
      ))}
      {itens.length === 0 && (
        <li className="text-xs text-ink-faint py-2">sem dado</li>
      )}
    </ol>
  );
}
