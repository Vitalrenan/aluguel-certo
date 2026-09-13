# Coleta, tratamento, refino e treino — Cloud Run Job.
#
# UM JOB, NÃO UM SERVIÇO. Estes processos rodam e terminam. Um serviço ficaria
# de pé entre coletas mensais cobrando por ociosidade, e precisaria de um
# endpoint só para ser disparado.
#
# O COMANDO VEM NA EXECUÇÃO, não aqui. Uma imagem por etapa multiplicaria por
# seis o que tem de ser construído e publicado, e as seis compartilham a mesma
# biblioteca. O `ENTRYPOINT` é o Python; o job diz qual módulo rodar.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Chrome. Necessário só para as plataformas cujo caminho de descoberta é
# renderizado no cliente -- sem ele esses adapters devolvem zero anúncios, que
# é a falha silenciosa clássica: status 200, lista vazia, nenhuma exceção.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && apt-get purge -y wget gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY engenharia/pyproject.toml /app/pyproject.toml
COPY engenharia/comum /app/comum
COPY engenharia/coleta /app/coleta
COPY engenharia/tratamento /app/tratamento
COPY engenharia/refino /app/refino
COPY engenharia/modelo /app/modelo
COPY engenharia/config /app/config
RUN pip install -e /app

# O lago chega por volume do Cloud Storage. Os processos já recebem `--raw-dir`,
# `--entrada` e `--saida`, então o caminho montado é o único ajuste.
ENV ALUGUELCERTO_LAGO=/lago

# Sem CMD com `--write`. Persistir é decisão de quem executa, e um padrão que
# grava transformaria um teste de fumaça numa escrita no lago de produção.
ENTRYPOINT ["python", "-m"]
CMD ["coleta.anuncios.coletar", "--help"]
