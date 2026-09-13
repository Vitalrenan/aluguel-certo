"use client";
import React from 'react';
import dynamic from 'next/dynamic';

/**
 * Área do mapa.
 *
 * Leaflet toca `window` no import do módulo, e `"use client"` NÃO impede o
 * prerender — componente de cliente ainda é renderizado no servidor para o HTML
 * inicial. Sem `ssr: false`, `next build` quebra com
 * "ReferenceError: window is not defined" ao gerar /dashboard.
 *
 * O `MapPinCard` que morava aqui foi removido em 2026-09-10: estava declarado e
 * nunca usado, e carregava as duas únicas sombras fora dos dois níveis do
 * design system (`shadow-xl` e `shadow-lg`).
 */
const PriceMap = dynamic(() => import('@/components/dashboard/PriceMap'), {
  ssr: false,
  loading: () => (
    <div className="flex-1 flex items-center justify-center text-sm text-ink-faint">
      carregando mapa...
    </div>
  ),
});

export default function MapArea() {
  return (
    <div className="flex-1 relative bg-canvas flex overflow-hidden">
      <PriceMap />
    </div>
  );
}
