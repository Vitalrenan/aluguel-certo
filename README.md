# refatoramento

Ambiente novo da esteira, criado em 2026-09-12. Três pastas, e a fronteira entre elas é
a mesma do documento de arquitetura: quem produz dado, quem serve dado e quem desenha tela.

| pasta | responsabilidade | não faz |
|---|---|---|
| [`engenharia/`](engenharia/) | coleta, tratamento, refino, treino do modelo | não atende HTTP |
| [`backend/`](backend/) | lê a camada refinada, resolve o modelo da cidade, serve a API | não calcula feature, não escreve no lago |
| [`frontend/`](frontend/) | as telas | não fala com o lago nem com o modelo |

O repositório antigo continua onde está enquanto a migração corre. Nada em
`data/data_collection/`, `serving/` ou `servicos/` é apagado antes da fase 6.

---

## Por que um ambiente novo em vez de refatorar no lugar

Três razões, todas medidas:

1. **21 arquivos em `pipeline/ml/` fixam o caminho da ABT no corpo.** Refatorar no lugar
   obriga a decidir o destino deles antes de qualquer outra coisa. Aqui eles ficam parados
   no repositório antigo, como registro do experimento que produziram.
2. **A suíte atual tem 566 testes e está verde.** Ela continua verde durante a migração
   inteira, porque nada que ela testa muda de lugar.
3. **`serving/` importa `pipeline/` por `sys.path`.** O acoplamento é real e correto — o
   serviço não pode reimplementar feature — mas a forma é frágil. O ambiente novo nasce
   com `pyproject.toml`, e a dependência vira importação de biblioteca.

## Decisões que fundaram esta pasta

Respondidas em 2026-09-12, e registradas aqui porque o código todo depende delas.

| # | decisão | resposta |
|---|---|---|
| D1 | os 21 scripts de experimento | congelados no repositório antigo |
| D2 | onde moram os coletores | padronizados como microserviço, dentro de `engenharia/coleta/` |
| D3 | partição da camada crua | **ano / mês / dia**; a agregação mensal acontece no tratamento |
| D4 | cidades com modelo | **apenas Santos e São Paulo**; sem modelo agrupado de reserva |

D4 tem consequência de tela: cidade sem modelo aparece na lista e não estima. Ver
[`frontend/README.md`](frontend/README.md).

## Documentos irmãos

| arquivo | papel |
|---|---|
| `../arquitetura.html` | o desenho da esteira |
| `../plano-de-execucao.html` | as atividades e as provas de saída |
| `../CLAUDE.md` | as regras invioláveis, que valem aqui igual |
