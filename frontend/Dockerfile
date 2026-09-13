# Frontend Next.js — Cloud Run Service.
#
# TRÊS ESTÁGIOS. O `node_modules` de desenvolvimento tem centenas de MB e nada
# disso é preciso para servir; só o que o build produz vai para a imagem final.

# --- dependências ----------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` e não `npm install`: instala exatamente o que o lock diz. `install`
# pode resolver uma versão diferente da que foi testada, e a diferença só
# aparece em produção.
RUN npm ci

# --- build -----------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

# A URL da API entra no BUILD, não na execução. O cliente é quem chama o
# backend, e `NEXT_PUBLIC_*` é embutida no bundle na compilação -- defini-la
# como variável de ambiente do contêiner não teria efeito nenhum, e a chamada
# sairia para `undefined/...`.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN npm run build

# --- execução --------------------------------------------------------------
FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# Usuário sem privilégio. O Cloud Run isola o contêiner, mas rodar como root
# dentro dele não compra nada e é um passo a menos de defesa.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

# `public/` NÃO é copiada: este projeto não tem essa pasta. Um `COPY` de
# diretório inexistente falha o build, e o erro fala de caminho e não da pasta
# que ninguém criou. Se ela passar a existir, acrescente a linha.
# O logotipo vive em `logo/` e é importado pelo bundle, não servido como
# estático solto.

USER nextjs

# O Cloud Run injeta PORT. O servidor do Next lê `PORT`, então não há o que
# traduzir -- mas `HOSTNAME` precisa ser 0.0.0.0, senão ele escuta em localhost
# e o balanceador nunca alcança o contêiner.
ENV PORT=8080 HOSTNAME=0.0.0.0
EXPOSE 8080

CMD ["node", "server.js"]
