import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `standalone` para o contêiner. Sem isto a imagem precisa carregar o
  // `node_modules` inteiro -- centenas de MB para servir uma aplicação que,
  // compilada, é uma fração disso. O Next monta em `.next/standalone` só o que
  // o servidor de fato importa.
  output: "standalone",
};

export default nextConfig;
