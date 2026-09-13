// frontend/components/dashboard/PriceMap.tsx
"use client";
import React from "react";
import { CircleMarker, MapContainer, TileLayer, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";

import { useBairros } from "@/hooks/useDashboardData";
import type { Bairro } from "@/lib/api";

/**
 * Mapa de bairros, com os pontos da NOSSA base.
 *
 * A versão anterior desenhava o contorno do Brasil vindo de um GeoJSON no
 * GitHub e não tinha nenhum dado nosso. Agora cada círculo é um bairro, com
 * raio pelo número de imóveis e cor pela faixa de R$/m².
 *
 * A REGRA QUE GOVERNA ESTE COMPONENTE: bairro sem coordenada NÃO é desenhado.
 * Ele não recebe o centroide da cidade nem o de um vizinho. Ponto inventado no
 * mapa é indistinguível de um medido, e o mapa é a peça da tela em que o
 * usuário menos confere. Quantos ficaram de fora aparece no rodapé.
 *
 * Este módulo é carregado por `MapArea` com `dynamic(..., { ssr: false })`: o
 * Leaflet toca `window` no import e derruba o prerender do `next build`.
 */

const COR: Record<string, string> = {
  abaixo: "#2563eb",
  media: "#7c3aed",
  acima: "#db2777",
};

const brl = (v: number) =>
  v.toLocaleString("pt-BR", { style: "currency", currency: "BRL",
                              maximumFractionDigits: 0 });

function raio(imoveis: number): number {
  // Raiz quadrada: a ÁREA do círculo fica proporcional à contagem, que é como
  // o olho compara. Raio proporcional exageraria os bairros grandes.
  return Math.max(4, Math.min(26, Math.sqrt(imoveis) * 1.1));
}

function centro(bairros: Bairro[]): [number, number] {
  const pts = bairros.filter((b) => b.lat != null && b.lon != null);
  if (!pts.length) return [-23.96, -46.33];       // Santos, só para abrir
  const lat = pts.reduce((s, b) => s + (b.lat as number), 0) / pts.length;
  const lon = pts.reduce((s, b) => s + (b.lon as number), 0) / pts.length;
  return [lat, lon];
}

export default function PriceMap() {
  const { bairros, error, isLoading } = useBairros(undefined, true);

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center p-8 text-center">
        <div>
          <div className="text-sm font-semibold text-ink-soft mb-1">
            Mapa indisponível
          </div>
          <div className="text-xs text-ink-faint max-w-xs">{error.message}</div>
        </div>
      </div>
    );
  }

  if (isLoading || !bairros) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-ink-faint">
        carregando bairros…
      </div>
    );
  }

  const pontos = bairros.bairros.filter((b) => b.lat != null && b.lon != null);
  const foraDoMapa =
    bairros.resumo.n_bairros - bairros.resumo.n_bairros_com_coordenada;

  return (
    <div className="flex-1 relative">
      <MapContainer
        center={centro(pontos)}
        zoom={11}
        scrollWheelZoom
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {pontos.map((b) => (
          <CircleMarker
            key={`${b.cidade}|${b.nome}`}
            center={[b.lat as number, b.lon as number]}
            radius={raio(b.imoveis)}
            pathOptions={{
              color: COR[b.faixa ?? "media"] ?? "#7c3aed",
              fillColor: COR[b.faixa ?? "media"] ?? "#7c3aed",
              fillOpacity: 0.45,
              weight: 1.5,
            }}
          >
            <Tooltip>
              <div className="text-xs">
                <div className="font-semibold">{b.nome}</div>
                <div className="text-ink-soft">{b.cidade}</div>
                <div className="mt-1">{b.imoveis} imóveis</div>
                <div>mediana {brl(b.preco_mediano)}</div>
                {b.preco_m2_mediano && <div>{brl(b.preco_m2_mediano)}/m²</div>}
                <div className="text-ink-faint mt-1">
                  centroide de {b.n_geo} imóveis ({b.origem_geo})
                </div>
              </div>
            </Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>

      {/* O que o mapa NÃO mostra fica escrito, não escondido. */}
      <div className="absolute bottom-3 left-3 z-[400] bg-surface/90 backdrop-blur px-3 py-2 rounded-[var(--radius-control)] shadow text-[10px] text-ink-soft leading-relaxed max-w-[280px]">
        {pontos.length} bairros no mapa
        {foraDoMapa > 0 && (
          <> · <span className="text-ink font-semibold">{foraDoMapa} sem
          coordenada</span>, não desenhados</>
        )}
        <div className="mt-1">
          geocodificação {bairros.geocodificacao.fonte}:{" "}
          {bairros.geocodificacao.por_cep} por CEP,{" "}
          {bairros.geocodificacao.por_logradouro} por logradouro
        </div>
      </div>
    </div>
  );
}
