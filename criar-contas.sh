#!/usr/bin/env bash
#
# Cria as contas de serviço que o Cloud Run usa em execução.
#
#   gcloud auth login        # com a conta dona do projeto
#   ./criar-contas.sh
#
# UMA CONTA, E ELA SÓ LÊ. Só a API vai a produção; a engenharia roda local e
# escreve no bucket com a credencial de quem a executa. Nada em produção precisa
# de escrita, e uma identidade com escrita que ninguém usa é superfície sem
# contrapartida.
#
# O ESCOPO É O BUCKET, NÃO O PROJETO. `roles/storage.objectUser` concedido no
# projeto alcança todo bucket que existir depois. Concedido no bucket, alcança
# este.
#
# NENHUMA CHAVE JSON É GERADA AQUI, e isso é deliberado. O Cloud Run entrega a
# credencial à identidade em tempo de execução. Chave em arquivo é o que se
# vaza, e o projeto já tem uma em disco -- não vamos criar a segunda.

set -euo pipefail

PROJETO="${PROJETO:-aluguelcerto}"
BUCKET="${BUCKET:-aluguelcerto-lake}"

SA_API="aluguelcerto-api@${PROJETO}.iam.gserviceaccount.com"

gcloud config set project "${PROJETO}" >/dev/null

cria() {
  local id="$1" titulo="$2" descricao="$3"
  if gcloud iam service-accounts describe "${id}@${PROJETO}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    echo "  ${id}: já existe"
  else
    gcloud iam service-accounts create "${id}" \
      --display-name="${titulo}" --description="${descricao}"
    echo "  ${id}: criada"
  fi
}

echo "Contas de execução"
cria aluguelcerto-api  "Aluguel Certo — API" \
     "Serviço de estimativa. Somente leitura da camada refinada."

echo
echo "Papel, no bucket e não no projeto"

# objectViewer: só leitura. A API nunca escreve no lago.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_API}" \
  --role="roles/storage.objectViewer" >/dev/null
echo "  api  -> storage.objectViewer em gs://${BUCKET}"

cat <<FIM

Pronto. Nenhuma chave foi gerada.

O QUE NÃO FOI CRIADO, e por quê: uma conta para PUBLICAR. Ela precisaria de
poder sobre Artifact Registry, Cloud Build e Cloud Run, e viveria como arquivo
de chave -- exatamente o que estas duas evitam. Enquanto o deploy for manual,
ele roda sob a sua conta de usuário. Quando houver CI, a via certa é Workload
Identity Federation, que autentica o GitHub sem chave nenhuma.

Próximo passo:
  ./deploy.sh tudo
FIM
