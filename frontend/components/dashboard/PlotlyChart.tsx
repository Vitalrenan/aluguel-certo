"use client";

import dynamic from "next/dynamic";
// O pacote exporta `PlotParams`, nao `PlotlyProps` -- ver
// node_modules/react-plotly.js/dist/index.d.ts
import type { PlotParams } from "react-plotly.js";

// Lazy‑load Plotly to avoid heavy bundle impact
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

export default function PlotlyChart({ data, layout, config }: PlotParams) {
  return <Plot data={data} layout={layout} config={config} />;
}
