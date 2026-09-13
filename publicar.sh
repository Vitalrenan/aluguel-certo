#!/usr/bin/env bash
#
# Sobe o lago local para o bucket que a API lê.
#
#   ./publicar.sh              # mostra o que mudaria, não envia
#   ./publicar.sh --write
#
# A ENGENHARIA RODA LOCAL, sempre. Ela não vai para o Cloud Run: a coleta é
# mensal, demora, depende de Chrome e de paciência com o site da fonte. Pagar
# contêiner para isso, e depurar scraping por log de nuvem, troca um problema
# fácil por um caro.
#
# O QUE VAI À NUVEM É O RESULTADO. Este script é a fronteira: tudo antes dele é
# disco local, tudo depois é o que a API serve.
#
# A CAMADA CRUA NÃO SOBE. Ela é o registro do que a fonte disse, e existe para
# reprocessar quando a regra muda -- trabalho que acontece aqui, na máquina que
# tem o código. Subir 108 MB de CNEFE que ninguém em produção lê seria pagar
# armazenamento por um backup que o disco já é.

set -euo pipefail

BUCKET="${BUCKET:-aluguelcerto-lake}"
PREFIXO="${PREFIXO:-refatoramento}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/engenharia" && pwd)"

ESCREVE=0
[ "${1:-}" = "--write" ] && ESCREVE=1

# O que a API lê, e só. `03_refined` para as tabelas, `modelos` para os
# boosters e os cartões.
CAMADAS=(
  "dados/03_refined|${PREFIXO}/03_refined"
  "modelos|${PREFIXO}/modelos"
)

echo "======================================================================"
echo "publicar -- lago local  ->  gs://${BUCKET}/${PREFIXO}"
echo "======================================================================"

# CONFERE ANTES DE SUBIR. Publicar um modelo sem cartão deixaria a API pulá-lo
# na resolução e responder "modelo ausente" para uma cidade que tem modelo --
# sintoma que parece falha de publicação e é falha de conteúdo.
faltando=0
while IFS= read -r -d '' pasta; do
  [ -f "${pasta}/model_card.json" ] || { echo "SEM CARTÃO: ${pasta}"; faltando=1; }
done < <(find "${RAIZ}/modelos" -mindepth 4 -maxdepth 4 -type d -print0 2>/dev/null)
[ "${faltando}" -eq 0 ] || { echo "Recusando publicar."; exit 1; }

for linha in "${CAMADAS[@]}"; do
  origem="${RAIZ}/${linha%%|*}"
  destino="gs://${BUCKET}/${linha##*|}"
  [ -d "${origem}" ] || { echo "PULANDO ${origem}: não existe"; continue; }

  echo
  echo "  ${linha%%|*}  ->  ${destino}"
  if [ "${ESCREVE}" -eq 1 ]; then
    # `--delete-unmatched-destination-objects`: o destino vira espelho da
    # origem. Sem isto um modelo removido localmente continuaria servido, e a
    # API responderia com um artefato que já não existe no repositório.
    gcloud storage rsync -r --delete-unmatched-destination-objects \
      "${origem}" "${destino}"
  else
    gcloud storage rsync -r --dry-run "${origem}" "${destino}" 2>&1 | tail -20
  fi
done

if [ "${ESCREVE}" -eq 0 ]; then
  echo
  echo "sem --write: nada foi enviado."
  exit 0
fi

echo
echo "Conferindo contagem local contra o bucket:"
for linha in "${CAMADAS[@]}"; do
  origem="${RAIZ}/${linha%%|*}"
  destino="gs://${BUCKET}/${linha##*|}"
  loc=$(find "${origem}" -type f | wc -l)
  rem=$(gcloud storage ls -r "${destino}/**" 2>/dev/null | grep -v ':$' | grep -c . || echo 0)
  estado=$([ "${loc}" = "${rem}" ] && echo ok || echo DIVERGE)
  printf "  %-16s local %4s   bucket %4s   %s\n" "${linha%%|*}" "${loc}" "${rem}" "${estado}"
done

cat <<FIM

A API lê o bucket por volume montado e não guarda cache, então a próxima
requisição já vê o que subiu. Não é preciso republicar o serviço.
FIM
