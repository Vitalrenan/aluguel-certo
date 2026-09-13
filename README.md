# Aluguel Certo

Estimador de preço de aluguel e venda de imóveis, com modelo por cidade.

Três pastas, e a fronteira entre elas é quem produz dado, quem serve dado e quem desenha
tela.

| pasta | responsabilidade | não faz |
|---|---|---|
| [`engenharia/`](engenharia/) | coleta, tratamento, refino, treino | não atende HTTP |
| [`backend/`](backend/) | lê a camada refinada, resolve o modelo da cidade, serve a API | não calcula feature, não escreve no lago |
| [`frontend/`](frontend/) | as telas | não fala com o lago nem com o modelo |

---

## Como isso funciona

Quatro fontes entram pela camada crua, duas tabelas saem da refinada, e um modelo por
cidade responde à API.

```
sites de imobiliárias ─┐
CNEFE · IBGE ──────────┤
FipeZAP ───────────────┼─→ 01_raw ─→ 02_processed ─→ 03_refined ─┬─→ modelos ─→ API ─→ tela
SIDRA · IBGE ──────────┘    dia        mês              mercado   └─→ ABT
```

**A camada crua é particionada por ano, mês e dia**, e é append-only. A mesma cidade
coletada três vezes em agosto produz três partições e nenhuma sobrescreve a outra.

**O tratamento colapsa os dias de um mês numa linha por imóvel** e nunca colapsa meses. O
mesmo anúncio em agosto e em outubro são duas observações de preço, e é isso que faz a
série existir.

**A camada refinada tem duas saídas e só duas.** Uma tabela de mercado que a API lê sem
junção, e a ABT pronta para treinar.

## Rodando

```bash
cd engenharia
pip install -e .

python -m coleta.anuncios.coletar --config config/fontes.yaml --write
python -m tratamento.anuncios.historico --todos --write
python -m tratamento.anuncios.amenidades --write
python -m refino.abt --write
python -m refino.mercado --write
python -m modelo.treinar --todos --write
```

Persistir exige `--write`. Sem ele todo processo relata e não grava.

```bash
cd engenharia && python -m unittest discover -s tests -t . -q    # 387 testes
cd backend  && python -m unittest discover -s tests -t . -q      # 10 testes
```

Nenhum teste toca a rede.

## As regras que não se negociam

Cada uma custou um erro real.

- **Nada de página capturada é persistido.** Buscar, verificar, descartar.
- **PII é descartada na ingestão**, antes de qualquer cache.
- **`robots.txt` é honrado.** Fonte que nos proíbe é descartada, nunca contornada, e não
  existe flag para desligar.
- **Nenhum serviço terceiro de scraping.** Rotação de IP e agente falso são evasão.
- **Zero linha é falha**, não sucesso.
- **Falha do portão de PII levanta** e nada é escrito. É a única exceção que não pode ser
  capturada.

## Duas decisões que explicam o resto

**Não existe modelo agrupado de reserva.** Cidade sem modelo não recebe estimativa
aproximada; recebe recusa com motivo. Servir um modelo de outra cidade e chamar aquilo de
estimativa produziria um número com a mesma cara de confiança de um medido, e ninguém na
tela conseguiria distinguir os dois.

**A disponibilidade é por par cidade × transação, não por cidade.** São Paulo tem volume de
venda e quase nada de locação — 5.628 linhas contra 2, medido em 12/09/2026. A cidade está
no escopo e o par São Paulo com locação não está.

## Nuvem

Dois serviços, escolhidos por custo: **Cloud Storage** para o lago inteiro e **Cloud Run**
para tudo que executa — um Job por etapa, um Service para a API. O que a nuvem precisa
guardar cabe em 146 MB, o que não justifica cluster, orquestrador nem data warehouse.

Job e Service rodam como conta de serviço e recebem credencial da plataforma, então não há
chave JSON para gerar, guardar ou vazar.

## Modelo

Um diretório por ano, mês, cidade e transação, com o booster central, os dois quantílicos, a
ordem das colunas, as categorias e o **model card**.

O cartão tem seis blocos, e o de `limites` é o que diz o que o modelo **não** deve ser
perguntado: uso pretendido, o que fica fora de escopo, a faixa de preço e de área treinadas,
e os bairros com n insuficiente. Sem ele, um pedido de doze milhões recebe um número com a
mesma confiança aparente de um de seiscentos mil.

Gravação de cartão inválido levanta. Um diretório com booster e sem cartão é pulado pela
resolução, em vez de servido sem proveniência.
