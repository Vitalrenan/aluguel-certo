# Processos da engenharia — mapa de migração

Levantado lendo o repositório antigo em 2026-09-12.

## A contagem não fecha em 31, e preciso da sua correção

Você disse 31 processos refatorados. Contei o repositório de várias formas e **nenhuma dá
31 processos vivos**:

| como contei | resultado |
|---|---|
| `.py` na raiz de `pipeline/` | 18 |
| `.py` na raiz de `pipeline/` **+** `utils/` | **31** |
| arquivos com `__main__`, fora de testes e `old_files/` | 25 |
| `.py` de `pipeline/` + `servicos/` + `serving/`, sem bibliotecas | 24 |
| todos os `.py` fora de teste e `venv` | 76 |
| **processos vivos que este mapa propõe** | **17** |

O único 31 que aparece é `pipeline/*.py` mais `pipeline/utils/*.py` — 18 mais 13. Mas os 13
de `utils/` são **biblioteca**, não processo: esquema, portão de privacidade, escritor do
lago, vocabulário. Eles vão para `comum/` e nenhum deles se executa sozinho.

Dos 18 da raiz, **8 são processos vivos**. Os outros 10 estão detalhados na segunda tabela.

**Se os seus 31 forem outro conjunto, me diga qual** — o risco aqui é eu deixar de migrar
algo que você usa, e esse é justamente o tipo de perda que não levanta erro.

---

## Os 17 processos propostos

### coleta/ — camada crua

| processo | vem de | estado |
|---|---|---|
| `coleta/anuncios/coletar.py` | `pipeline/collect.py` | porta, com a partição nova ano/mês/dia |
| `coleta/cnefe/coletar.py` | metade de `pipeline/construir_logradouros.py` | parte a escrita em duas |
| `coleta/fipezap/coletar.py` | `servicos/fipezap/coletar.py` | parte a escrita em duas |
| `coleta/sidra/coletar.py` | — | **novo**, depois do inventário |

### tratamento/ — camada tratada

| processo | vem de | estado |
|---|---|---|
| `tratamento/anuncios/normalizar.py` | `pipeline/treat.py` | porta |
| `tratamento/anuncios/historico.py` | `pipeline/construir_base.py` (P01) | **reescrito** — é a tabela histórica mensal |
| `tratamento/anuncios/amenidades.py` | `pipeline/enriquecer_amenities.py` + P02 | porta |
| `tratamento/cnefe/derivar.py` | outra metade de `construir_logradouros.py` | porta **+ o construtor de `cep_coord`, que não existe** |
| `tratamento/fipezap/derivar.py` | recorte de colunas do coletor atual | extrai |
| `tratamento/sidra/derivar.py` | — | **novo** |

`historico.py` é o processo central da refatoração. O atual monta a base com *o snapshot
mais recente de cada domínio* — cada domínio entra uma vez, e por construção o mesmo anúncio
nunca aparece duas vezes. O novo agrega os dias do mês numa linha por imóvel, e mantém meses
separados.

### refino/ — camada refinada

| processo | vem de | estado |
|---|---|---|
| `refino/mercado.py` | `pipeline/construir_indicadores_bairro.py` | **absorve** também FipeZAP, rendimento e SIDRA |
| `refino/abt.py` | `pipeline/construir_abt.py` | porta, muda só o destino |

### modelo/

| processo | vem de | estado |
|---|---|---|
| `modelo/ablacao.py` | usa `ml/executor.py` | **novo** — mede o piso de n por cidade |
| `modelo/treinar.py` | `pipeline/treina_final.py` | porta, grava em `{ano}/{mes}/{cidade}/{alvo}/` |
| `modelo/cartao.py` | bloco de `metadados.json` | **novo** — o model card, com o bloco `limites` |

### auditoria/

| processo | vem de | estado |
|---|---|---|
| `auditar.py` | skill `auditar-coleta` | vira processo versionado |
| `cobertura.py` | hoje espalhado por vários scripts | **novo** — taxa de preenchimento por campo, saída padrão |

---

## O que NÃO migra, e por quê

Dez dos 18 arquivos da raiz do pipeline. Ficam parados no repositório antigo; nenhum é
apagado.

| arquivo | motivo |
|---|---|
| `step_1_scraper.py` | legado pré-refatoração — raspador do Zap, substituído por `collect.py` |
| `step_2_enhancer.py` | legado da mesma sequência |
| `step_3_appender.py` | legado da mesma sequência |
| `step_4_dw.py` | legado — carga no BigQuery, com caminho de credencial fixo no corpo |
| `step_5_loader.py` | legado da mesma sequência |
| `modelo_lgbm.py` | experimento — superado por `treina_final.py`, que serializa o artefato |
| `rmse_puro.py` | medição pontual — o resultado está no MEDICOES.md |
| `sondar_cep.py` | sonda exploratória |
| `sondar_cep_bairro.py` | sonda exploratória |
| `sondar_endereco.py` | sonda exploratória |

Mais os **21 arquivos de `ml/fase*.py`**, congelados por D1.

As sondas e experimentos têm valor como registro: cada um responde uma pergunta que já foi
respondida, e o número está no MEDICOES.md. Portá-los custaria manutenção sem entregar nada.

---

## comum/ — biblioteca, não processo

Nenhum destes se executa sozinho. Vêm de `pipeline/utils/` e dos módulos de `pipeline/ml/`
que sobrevivem.

**Os nomes ficam em inglês.** A regra do projeto é código em inglês e documento em
português, e renomear dez módulos para `privacidade` e `lago` quebraria o vocabulário de
369 testes sem entregar nada.

| módulo | papel | por que é inviolável |
|---|---|---|
| `schema` | esquema e allowlist de campo | |
| `privacy` | portão de PII na ingestão | falha levanta e nada é escrito |
| `lake` | escrita particionada | exige `--write`; falha de escrita levanta |
| `vocab` | vocabulário controlado | |
| `normalizer` | forma canônica e `property_id` | a chave que sustenta a série mensal |
| `fetch`, `politeness`, `jsonld` | requisição, `robots.txt`, dado estruturado | `robots.txt` não tem flag para desligar |
| `config`, `amenidades_texto` | configuração de fonte, amenidade em texto livre | |

Ficam para trás `utils/scraper.py` e `utils/data_manager.py`: só `old_files/` os importa.

Quando `modelo/` for migrado, entram também `formulario` — **a definição de feature**, que
o backend importa e nunca reimplementa — mais `dados`, `intervalo`, `dedup` e `geo`.

Os 9 adapters de plataforma foram para `coleta/anuncios/adapters/`, sem mudança de lógica.

---

## Estado da migração — 12/09/2026

**Concluída.** Os 17 processos existem e rodaram com dado real.

| parte | estado | prova |
|---|---|---|
| `comum/` — biblioteca, 21 módulos | migrada | |
| coleta — anúncios, CNEFE, FipeZAP, SIDRA | **4 de 4** | rodaram e gravaram |
| tratamento — anúncios, CNEFE, FipeZAP, SIDRA | **6 de 6** | rodaram e gravaram |
| refino — ABT e mercado | **2 de 2** | 13.291 linhas e 1.059 linhas |
| modelo — cartão, núcleo, treino | **3 de 3** | 3 modelos gravados |
| backend | migrado | 10 testes |
| frontend | copiado + 1 mudança | `tsc --noEmit` limpo |
| suíte da engenharia | **387 testes, verdes** | |
| suíte do backend | **10 testes, verdes** | |
| suíte do repositório antigo | **566 testes, verdes** | nada foi quebrado |

### O que rodou, com número

| camada | resultado |
|---|---|
| crua — anúncios | 24 partições migradas para ano/mês/dia, 24.038 linhas |
| crua — CNEFE | 7 municípios, 6,7 milhões de endereços, 9 colunas de 34 |
| crua — FipeZAP | 26.366 linhas, 57 cidades, 2008-01 a 2026-08 |
| crua — SIDRA | 2.244 linhas, IPCA nacional, 1979-12 a 2026-08 |
| tratada — histórico mensal | 14.842 linhas, 44 arquivos, agosto 8.354 e setembro 6.488 |
| refinada — ABT | 13.291 de treino, 1.475 de holdout, 207 preditores |
| refinada — mercado | 1.059 linhas, 41 cidades, 989 bairros, 86,4% com coordenada |
| modelos | Santos venda 5.819, Santos locação 806, São Paulo venda 5.638 |

### Três defeitos que a migração produziu e os testes pegaram

**A busca do que já veio no mês deixou de casar.** O padrão era
`{cidade}/{mes}-*`, que casava o token `2026-08-27` inteiro. Contra
`{ano}/{mes}/{dia}` ele não casa nada — e zero partições encontradas devolve
zero conhecidos, faz a coleta incremental recoletar tudo, e não levanta exceção.

**Grafias da mesma cidade colidiram no mesmo caminho.** `SP/Santos`,
`sp/Santos` e `sp/santos` davam o mesmo diretório, e o último a gravar apagava
os anteriores: **8.108 de 14.842 linhas perdidas**, sem erro, com a tabela
parecendo correta. O agrupamento passou a ser pela chave já normalizada, e dois
testes travam isso.

**O nome da cidade com acento não casava com a ABT.** `cidades.yaml` diz
`São Paulo` e a ABT diz `Sao Paulo`. O treinador reportou `treino 0` como se a
cidade não tivesse dado — um arquivo de configuração e uma tabela discordando
de grafia, lendo-se como cidade vazia.

### Aberto

`DEFAULT_RAW_DIR` aponta para `gs://dataacquisition/refatoramento/01_raw/listings`,
prefixo próprio dentro do bucket existente. Bucket separado não foi possível: a
conta de serviço tem permissão de objeto, não de bucket.

O relógio da máquina está 8 minutos adiantado, o que faz o Google recusar
autenticação de conta de serviço com mensagem que parece chave inválida.
