#!/usr/bin/env bash
#
# Cria as contas de serviço que o Cloud Run usa em execução.
#
#   gcloud auth login        # com a conta dona do projeto
#   ./criar-contas.sh
#
# DUAS CONTAS, NÃO UMA. Os jobs escrevem no lago e a API só lê. Uma conta só,
# com escrita, deixaria a API capaz de sobrescrever a camada refinada que ela
# deveria apenas servir -- e o único freio seria o `readonly` do volume, que é
# configuração de deploy e não identidade. Quem remover essa flag um dia não
# encontraria nenhuma outra barreira.
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
BUCKET="${BUCKET:-dataacquisition}"

SA_JOBS="aluguelcerto-jobs@${PROJETO}.iam.gserviceaccount.com"
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
cria aluguelcerto-jobs "Aluguel Certo — jobs" \
     "Coleta, tratamento, refino e treino. Lê e escreve no lago."
cria aluguelcerto-api  "Aluguel Certo — API" \
     "Serviço de estimativa. Somente leitura da camada refinada."

echo
echo "Papéis, no bucket e não no projeto"

# objectUser: ler, criar e apagar objeto. Os jobs precisam de criar; apagar
# entra porque o tratamento REESCREVE a partição do mês ao reconstruí-la.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_JOBS}" \
  --role="roles/storage.objectUser" >/dev/null
echo "  jobs -> storage.objectUser em gs://${BUCKET}"

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
