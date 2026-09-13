#!/usr/bin/env bash
#
# Publica em produção: a API e as telas.
#
#   ./deploy.sh api       # o backend
#   ./deploy.sh frontend  # as telas
#   ./deploy.sh tudo
#
# SÓ BACKEND E FRONTEND VÃO A PRODUÇÃO. A engenharia roda local, sempre, e
# empurra o resultado para o bucket com `./publicar.sh --write`.
#
# NÃO HÁ CLOUD RUN JOB. A coleta é mensal, demora, depende de Chrome e de
# paciência com o site da fonte. Pagar contêiner para isso, e depurar scraping
# por log de nuvem, troca um problema fácil por um caro.
#
# DOIS SERVIÇOS, por decisão de custo: Cloud Storage e Cloud Run. O Artifact
# Registry entra porque é de onde o Cloud Run busca a imagem -- não é escolha
# de arquitetura, é o que o Cloud Run exige para existir.
#
# O LAGO CHEGA POR VOLUME, não por biblioteca de nuvem. O Cloud Run monta o
# bucket e o código continua lendo caminho de arquivo -- os mesmos caminhos dos
# testes. `gs://` dentro do código criaria um ramo exercitado só em produção.
#
# NENHUMA CHAVE JSON. O Service roda como conta de serviço e recebe credencial
# da plataforma. Não há arquivo para gerar, guardar ou vazar.

set -euo pipefail

PROJETO="${PROJETO:-aluguelcerto}"
REGIAO="${REGIAO:-southamerica-east1}"
BUCKET="${BUCKET:-aluguelcerto-lake}"
PREFIXO="${PREFIXO:-refatoramento}"
REPO="${REPO:-aluguel-certo}"

# Uma identidade só, e ela só LÊ. A engenharia escreve no bucket da máquina
# local, com a credencial de quem roda; nada em produção precisa de escrita.
SA_API="${SA_API:-aluguelcerto-api@${PROJETO}.iam.gserviceaccount.com}"

NUM_PROJETO="$(gcloud projects describe "${PROJETO}" --format='value(projectNumber)' 2>/dev/null)"
IMG="${REGIAO}-docker.pkg.dev/${PROJETO}/${REPO}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# BARRA DOBRADA DE PROPÓSITO. O Git Bash no Windows reescreve argumento que
# parece caminho absoluto: `/lago-bucket` chega ao gcloud como
# `C:/Program Files/Git/lago-bucket`, e o Cloud Run recusa com "should be a
# valid unix absolute path" -- mensagem que acusa o valor e não quem o alterou.
# O MSYS traduz `//x` de volta para `/x`, e em Linux o `//x` é equivalente.
#
# `MSYS_NO_PATHCONV=1` resolveria o argumento e quebraria o próprio lançador do
# gcloud, que é um script shell e depende da conversão para achar o `gcloud.py`.
MONTE="//lago-bucket"

# ---------------------------------------------------------------------------

preparar() {
  gcloud config set project "${PROJETO}" >/dev/null
  gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
      cloudbuild.googleapis.com --project "${PROJETO}"

  # A conta tem de existir ANTES do deploy. O Cloud Run aceita
  # `--service-account` apontando para conta inexistente e só falha no primeiro
  # arranque, o que aparece como erro de execução e não de configuração.
  gcloud iam service-accounts describe "${SA_API}" >/dev/null 2>&1 \
    || { echo "FALTA a conta ${SA_API}. Rode ./criar-contas.sh"; exit 1; }

  gcloud artifacts repositories describe "${REPO}" --location "${REGIAO}" >/dev/null 2>&1 \
    || gcloud artifacts repositories create "${REPO}" \
         --repository-format=docker --location="${REGIAO}" \
         --description="Imagens do Aluguel Certo"

  # Região do bucket e do Cloud Run têm de bater. Montar bucket de outra região
  # funciona e cobra transferência a cada leitura -- aparece na fatura e não no
  # log, que é o pior lugar para um problema aparecer.
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
    --set-env-vars "^;^ALUGUELCERTO_REFINED=${MONTE}/${PREFIXO}/03_refined;ALUGUELCERTO_MODELOS=${MONTE}/${PREFIXO}/modelos" \
    --allow-unauthenticated \
    --min-instances 0 \
    --memory 1Gi --cpu 1 --timeout 60s

  # `--min-instances 0` é o que faz o custo acompanhar o uso. O preço é o
  # arranque a frio da primeira requisição depois de um período parado.
  #
  # O volume é `readonly`: a API lê a camada refinada e nunca escreve no lago.
  # Montar com escrita daria ao serviço poder que o desenho não lhe atribui.
}

frontend() {
  # A URL DA API ENTRA NO BUILD. O cliente é quem chama o backend, e
  # `NEXT_PUBLIC_*` é embutida no bundle na compilação -- defini-la como
  # variável de ambiente do contêiner não teria efeito, e a chamada sairia para
  # `undefined/...`, que no navegador vira erro de rede sem explicação.
  local api_url
  api_url="$(gcloud run services describe aluguel-certo-api \
      --region "${REGIAO}" --format='value(status.url)' 2>/dev/null || true)"
  [ -n "${api_url}" ] || { echo "A API precisa estar no ar antes das telas."; exit 1; }
  echo "API em ${api_url}"

  gcloud builds submit "${RAIZ}" \
    --config "${RAIZ}/cloudbuild-frontend.yaml" \
    --substitutions "_IMAGEM=${IMG}/frontend:latest,_API_URL=${api_url}"

  gcloud run deploy aluguel-certo-frontend \
    --image "${IMG}/frontend:latest" \
    --region "${REGIAO}" \
    --allow-unauthenticated \
    --min-instances 0 \
    --memory 512Mi --cpu 1 --timeout 60s

  # Sem conta de serviço própria e sem volume: as telas não tocam o bucket nem
  # o modelo. Tudo que elas sabem vem da API, por HTTP.

  # CORS. O navegador chama a API direto, de outra origem, e sem isto toda
  # requisição do frontend é bloqueada pelo navegador -- com a API respondendo
  # 200 nos logs, que é onde se perde tempo procurando.
  # AS DUAS URLs, não uma. O Cloud Run publica o serviço em dois endereços: o
  # formato novo, com o número do projeto, e o legado, com hash. `status.url`
  # devolve só o legado, e liberar só ele faz o navegador bloquear quem abriu
  # pelo outro -- com a API respondendo 200 no log, que é onde se perde tempo
  # procurando.
  local fe_legado fe_novo
  fe_legado="$(gcloud run services describe aluguel-certo-frontend \
      --region "${REGIAO}" --format='value(status.url)')"
  fe_novo="https://aluguel-certo-frontend-${NUM_PROJETO}.${REGIAO}.run.app"

  # `^;^` troca o separador da lista para `;`, porque as URLs não têm vírgula
  # mas a lista tem -- sem isso o gcloud parte cada URL num par chave=valor.
  gcloud run services update aluguel-certo-api --region "${REGIAO}" \
    --update-env-vars "^;^ALUGUELCERTO_ORIGINS=${fe_legado},${fe_novo}"
  echo "CORS da API liberado para:"
  echo "  ${fe_legado}"
  echo "  ${fe_novo}"
}

case "${1:-tudo}" in
  api)      preparar; api ;;
  frontend) preparar; frontend ;;
  tudo)     preparar; api; frontend ;;
  *) echo "uso: $0 [api|frontend|tudo]"; exit 2 ;;
esac
