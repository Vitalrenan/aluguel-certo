# engenharia

Tudo que produz dado. Nenhum processo daqui atende HTTP.

```
config/       fontes.yaml, cidades.yaml — o que coletar e para quem há modelo
painel.py     painel administrativo da coleta, HTML local
comum/        biblioteca compartilhada: schema, privacidade, lago, vocabulário
coleta/       01_raw  — um microserviço por fonte
tratamento/   02_processed — limpeza, dedup e a tabela histórica mensal
refino/       03_refined — mercado e ABT
modelo/       treino, ablação e model card
dados/        as quatro camadas do lago
modelos/      artefatos treinados, {ano}/{mes}/{cidade}/{alvo}/
tests/        nenhum toca a rede
```

---

## A lista de cidades é fechada

**Coleta de anúncio acontece em quinze cidades e em mais nenhuma.** A lista vive
em [`comum/cidades_alvo.py`](comum/cidades_alvo.py), como objetos, e o porquê
está em [`COLETA.md`](COLETA.md).

A regra é aplicada na INGESTÃO, por `cidades_alvo.esta_no_alvo()`, antes de
qualquer linha ir ao disco. Filtrar depois já teria custado requisição no site
da fonte, e regra aplicada tarde demais é indistinguível de regra não aplicada.

A base tinha anúncio de 41 cidades e 36 delas com menos de vinte linhas. Cauda
que não treina modelo, não enche mapa e conta como volume em toda medição.

## O painel

```bash
python painel.py
```

HTML local e administrativo, que **não vai para a nuvem**: mostra saúde de
processo e contagem por cidade, que é informação de operação e não de produto.
Lê o disco, não a API -- perguntar ao serviço que consome a coleta mediria o
consumo, não a produção.

## A partição da camada crua — ano / mês / dia

```
dados/01_raw/
  listings/{uf}/{cidade}/{ano}/{mes}/{dia}/{plataforma}/{dominio}/listings.parquet
  cnefe/{cod_ibge}/{ano_censo}/enderecos.parquet
  fipezap/{ano}/{mes}/serie.parquet
  sidra/{tabela}/{ano}/{mes}/serie.parquet
```

Um diretório por **dia de coleta**. A mesma cidade coletada três vezes em agosto produz
três partições, e nenhuma sobrescreve a outra. A camada é append-only: corrigir aqui
apagaria a evidência e impediria reprocessar com regra nova.

Isto troca a partição antiga, que era uma data solta (`2026-08-27`). A hierarquia
ano/mês/dia é o que permite ler um mês inteiro sem varrer o lago.

## A tabela histórica — onde a agregação mensal acontece

`tratamento/` lê todas as partições de dia de um mês e produz **uma linha por imóvel por
mês**.

```
dados/02_processed/listings/{uf}/{cidade}/historico.parquet
    particionado por ano, mes
    chave: (property_id, ano, mes)
```

**A deduplicação é entre dias do mesmo mês.** O mesmo anúncio coletado em 27, 28 e 29 de
agosto é um imóvel em agosto, não três. Isto está medido: nos sete domínios de Santos com
duas coletas, a interseção de `property_id` entre 27/08 e 29/08 ficou entre 99,4% e 100%.
A chave sobrevive à recoleta.

**A deduplicação nunca é entre meses.** O mesmo anúncio em agosto e em setembro são duas
linhas, e é isso que faz a série histórica existir. Um dedup por `property_id` sem o mês na
chave devolve base limpa, destrói a série e não levanta exceção nenhuma. O teste que trava
isso é obrigatório.

### O que o colapso de dias guarda

Colapsar três dias em uma linha joga fora informação se for feito com descuido. A proposta:

| coluna | regra | por quê |
|---|---|---|
| atributos do imóvel | última observação do mês | área e quartos não mudam; se mudarem, o anúncio foi corrigido e a correção vale |
| `preco_primeiro` | primeira observação do mês | |
| `preco_ultimo` | última observação do mês | é o preço do mês para efeito de série |
| `n_dias_observado` | contagem de partições de dia | distingue imóvel visto uma vez de visto o mês inteiro |
| `preco_mudou_no_mes` | booleano derivado | reajuste dentro do mês é sinal, não ruído |

Sem `n_dias_observado`, um imóvel visto num único dia fica indistinguível de um visto
trinta vezes, e os dois pesam igual em qualquer mediana.

## A camada refinada

```
dados/03_refined/
  mercado/mercado_{ano}-{mes}.parquet    uma linha por cidade × bairro × mês
  abt/{alvo}/abt_{ano}-{mes}.parquet     grão de anúncio, pronta para treinar
  abt/{alvo}/holdout_{ano}-{mes}.parquet tocado uma vez, no treino final
```

## Regras que não mudam de lugar

Vêm do `CLAUDE.md` e valem aqui inteiras.

- Nenhuma página capturada é persistida. Buscar, verificar, descartar.
- PII é descartada na ingestão, antes de qualquer cache.
- `robots.txt` é honrado, e não existe flag para desligar.
- Nenhum serviço terceiro de scraping.
- Zero linha é falha, não sucesso.
- Falha do portão de PII levanta e nada é escrito. É a única exceção que não pode ser
  capturada.
- Persistir exige `--write`. `--dry-run` sempre vence.
