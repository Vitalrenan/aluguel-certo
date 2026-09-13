# API de estimativa — Cloud Run Service.
#
# SEM CHROME, ao contrário da imagem de coleta. O serviço não raspa página; ele
# carrega um booster e responde. Instalar o navegador aqui somaria ~400 MB de
# superfície que nunca é exercitada, e arrastaria o tempo de arranque a frio
# para dentro da primeira requisição de cada escala a zero.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libgomp: o runtime de OpenMP que o LightGBM carrega. A imagem `slim` não o
# traz, e a falta dele NÃO aparece no build nem no arranque -- `import lightgbm`
# só acontece quando o primeiro pedido carrega um booster. O sintoma foi 500 na
# estimativa com `/saude` e `/pronto` respondendo 200.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# A engenharia entra como BIBLIOTECA, não como cópia. O serviço não pode
# reimplementar feature: recalcular `area_por_quarto` com outro tratamento de
# divisão por zero devolve previsão plausível e errada, sem exceção.
#
# COPIA O PACOTE INTEIRO, embora a API só importe de `comum`. Copiar metade
# quebrava o build: o `pyproject.toml` declara os nove pacotes e o pip parava
# em `package directory 'coleta' does not exist`.
#
# A alternativa seria fazer o pyproject descobrir os pacotes presentes, e ela é
# pior: a mesma declaração instalaria coisas diferentes conforme o contexto, e
# um diretório esquecido viraria uma instalação menor sem ninguém notar. O que
# pesava nesta imagem era o Chrome, que continua fora; o código-fonte das
# outras etapas é texto e não muda a ordem de grandeza.
COPY engenharia /app/engenharia
RUN rm -rf /app/engenharia/dados /app/engenharia/modelos /app/engenharia/tests \
 && pip install -e /app/engenharia

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY backend/api /app/backend/api

WORKDIR /app/backend

# O LAGO E OS MODELOS NÃO ENTRAM NA IMAGEM. Eles chegam por volume montado do
# Cloud Storage, o que mantém o código lendo caminho de arquivo -- os mesmos
# caminhos que os testes usam. Embutir os parquets faria a imagem precisar de
# rebuild a cada coleta mensal, e o modelo servido seria o do dia do build.
ENV ALUGUELCERTO_REFINED=/lago/03_refined \
    ALUGUELCERTO_MODELOS=/lago/modelos \
    ALUGUELCERTO_CIDADES=/app/engenharia/config/cidades.yaml

# O Cloud Run injeta PORT. Fixar 8000 aqui faria o contêiner subir e o
# balanceador nunca achar a porta -- falha que se manifesta como timeout, não
# como erro de configuração.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT}
