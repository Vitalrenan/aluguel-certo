#!/usr/bin/env bash
#
# Publica a API e os jobs no Cloud Run.
#
#   ./deploy.sh api      # o Cloud Run Service
#   ./deploy.sh jobs     # os seis Cloud Run Jobs
#   ./deploy.sh tudo
#
# DOIS SERVIÇOS, por decisão de custo: Cloud Storage e Cloud Run. O Artifact
# Registry entra porque é de onde o Cloud Run busca a imagem -- não é escolha
# de arquitetura, é o que o Cloud Run exige para existir.
#
# O LAGO CHEGA POR VOLUME, não por biblioteca de nuvem. O Cloud Run monta o
# bucket e o código continua lendo caminho de arquivo -- os mesmos caminhos dos
# testes. A alternativa, `gs://` dentro do código, criaria um ramo que só é
# exercitado em produção: os testes rodariam contra disco e o que vai ao ar
# seria outro caminho, nunca testado.
#
# NENHUMA CHAVE JSON. Job e Service rodam como conta de serviço e recebem
# credencial da plataforma. Não há arquivo para gerar, guardar ou vazar.

set -euo pipefail

PROJETO="${PROJETO:-aluguelcerto}"
REGIAO="${REGIAO:-southamerica-east1}"
BUCKET="${BUCKET:-dataacquisition}"
PREFIXO="${PREFIXO:-refatoramento}"
REPO="${REPO:-aluguel-certo}"
# Duas identidades, criadas por `criar-contas.sh`. Os jobs escrevem no lago e a
# API só lê -- separar isso na identidade, e não só no `readonly` do volume, é o
# que impede a API de sobrescrever a camada que ela deveria apenas servir.
SA_JOBS="${SA_JOBS:-aluguelcerto-jobs@${PROJETO}.iam.gserviceaccount.com}"
SA_API="${SA_API:-aluguelcerto-api@${PROJETO}.iam.gserviceaccount.com}"

IMG="${REGIAO}-docker.pkg.dev/${PROJETO}/${REPO}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONTE="/lago-bucket"

# ---------------------------------------------------------------------------

confere_contas() {
  # As contas têm de existir ANTES do deploy. O Cloud Run aceita
  # `--service-account` apontando para conta inexistente e só falha no primeiro
  # arranque, o que aparece como erro de execução e não de configuração.
  local faltando=0
  for sa in "${SA_JOBS}" "${SA_API}"; do
    gcloud iam service-accounts describe "${sa}" >/dev/null 2>&1 \
      || { echo "FALTA a conta ${sa}"; faltando=1; }
  done
  [ "${faltando}" -eq 0 ] || { echo "Rode ./criar-contas.sh primeiro."; exit 1; }
}

preparar() {
  gcloud config set project "${PROJETO}" >/dev/null
  gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
      cloudbuild.googleapis.com --project "${PROJETO}"
  confere_contas

  gcloud artifacts repositories describe "${REPO}" --location "${REGIAO}" >/dev/null 2>&1 \
    || gcloud artifacts repositories create "${REPO}" \
         --repository-format=docker --location="${REGIAO}" \
         --description="Imagens do Aluguel Certo"

  # A região do bucket e a do Cloud Run têm de ser a mesma. Montar um bucket de
  # outra região funciona e cobra transferência a cada leitura -- aparece na
  # fatura e não no log, que é o pior lugar para um problema aparecer.
  local br
  br="$(gcloud storage buckets describe "gs://${BUCKET}" --format='value(location)' 2>/dev/null || echo '?')"
  if [ "$(echo "${br}" | tr 'A-Z' 'a-z')" != "$(echo "${REGIAO}" | tr 'A-Z' 'a-z')" ]; then
    echo "AVISO: bucket em '${br}', Cloud Run em '${REGIAO}'."
    echo "       Leitura entre regiões cobra transferência a cada requisição."
  fi
}

api() {
  gcloud builds submit "${RAIZ}" \
    --config "${RAIZ}/cloudbuild-api.yaml" \
    --substitutions "_IMAGEM=${IMG}/api:latest"

  gcloud run deploy aluguel-certo-api \
    --image "${IMG}/api:latest" \
    --region "${REGIAO}" \
    --service-account "${SA_API}" \
    --add-volume "name=lago,type=cloud-storage,bucket=${BUCKET},readonly=true" \
    --add-volume-mount "volume=lago,mount-path=${MONTE}" \
    --set-env-vars "ALUGUELCERTO_REFINED=${MONTE}/${PREFIXO}/03_refined,ALUGUELCERTO_MODELOS=${MONTE}/${PREFIXO}/modelos" \
    --allow-unauthenticated \
    --min-instances 0 \
    --memory 1Gi --cpu 1 --timeout 60s

  # `--min-instances 0` é o que faz o custo acompanhar o uso. O preço é o
  # arranque a frio da primeira requisição depois de um período parado, e com
  # um booster de poucos MB ele é tolerável para uma calculadora.
  #
  # O volume é `readonly`: a API lê a camada refinada e nunca escreve no lago.
  # Montar com escrita daria ao serviço poder que o desenho não lhe atribui.
}

jobs() {
  gcloud builds submit "${RAIZ}" \
    --config "${RAIZ}/cloudbuild-engenharia.yaml" \
    --substitutions "_IMAGEM=${IMG}/engenharia:latest"

  # Nome do job e argumentos, na ordem de dependência: nada de refino antes de
  # tratamento. Uma imagem só; o que muda é o comando.
  publica_job coleta-anuncios  "coleta.anuncios.coletar,--config,config/fontes.yaml,--write"
  publica_job coleta-cnefe     "coleta.cnefe.coletar,--write"
  publica_job coleta-fipezap   "coleta.fipezap.coletar,--write"
  publica_job coleta-sidra     "coleta.sidra.coletar,--write"
  publica_job tratamento       "tratamento.anuncios.historico,--todos,--write"
  publica_job refino           "refino.mercado,--write"

  cat <<FIM

Agendamento mensal, um por job. Exemplo:

  gcloud run jobs update aluguel-certo-coleta-anuncios \\
      --region ${REGIAO} --schedule '0 3 1 * *'

A ordem importa: coleta no dia 1, tratamento no dia 2, refino no dia 3. O
Cloud Run não encadeia jobs, então o espaçamento é o que garante que cada um
encontre a saída do anterior.
FIM
}

publica_job() {
  local nome="$1" args="$2"
  gcloud run jobs deploy "aluguel-certo-${nome}" \
    --image "${IMG}/engenharia:latest" \
    --region "${REGIAO}" \
    --service-account "${SA_JOBS}" \
    --add-volume "name=lago,type=cloud-storage,bucket=${BUCKET}" \
    --add-volume-mount "volume=lago,mount-path=${MONTE}" \
    --set-env-vars "ALUGUELCERTO_LAGO=${MONTE}/${PREFIXO}" \
    --args "${args}" \
    --max-retries 1 \
    --task-timeout 3600s \
    --memory 2Gi

  # `--max-retries 1` de propósito. Repetir coleta automaticamente dobra a
  # carga sobre o site da fonte, e a polidez é regra do projeto, não
  # configuração. Job que falha é lido e re-executado à mão.
}

case "${1:-tudo}" in
  api)   preparar; api ;;
  jobs)  preparar; jobs ;;
  tudo)  preparar; api; jobs ;;
  *) echo "uso: $0 [api|jobs|tudo]"; exit 2 ;;
esac
