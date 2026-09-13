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

WORKDIR /app

# A engenharia entra como BIBLIOTECA, não como cópia. O serviço não pode
# reimplementar feature: recalcular `area_por_quarto` com outro tratamento de
# divisão por zero devolve previsão plausível e errada, sem exceção.
COPY engenharia/pyproject.toml /app/engenharia/pyproject.toml
COPY engenharia/comum /app/engenharia/comum
COPY engenharia/config /app/engenharia/config
RUN pip install -e /app/engenharia

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
