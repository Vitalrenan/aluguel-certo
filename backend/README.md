# backend

Lê a camada refinada, resolve o modelo da cidade e serve a API. Não calcula feature e
não escreve no lago.

```
api/      as rotas
tests/    nenhum toca a rede
```

---

## O que ele lê, e só

| origem | para que |
|---|---|
| `engenharia/dados/03_refined/mercado/` | indicadores de cidade e de bairro |
| `engenharia/modelos/{ano}/{mes}/{cidade}/{alvo}/` | o modelo e o cartão |
| `engenharia/config/cidades.yaml` | quais pares cidade×alvo estimam |

Nenhum caminho de `02_processed`. Se aparecer um, a fase 4 não terminou.

## A resolução do modelo

Sai a variável de ambiente que fixava um segmento no arranque. Um serviço que escolhe o
segmento ao subir não atende uma tela com seletor de cidade.

A resolução passa a ser por pedido: o par **(cidade, alvo)** aponta para o diretório do
modelo, com ponteiro para a versão mais recente.

**Não há modelo agrupado de reserva.** Par sem modelo não recebe estimativa aproximada —
recebe uma recusa explícita, com a mensagem de `cidades.yaml`. Responder com um número
vindo de outra cidade seria indistinguível, na tela, de um número medido.

## Rotas

| rota | o que faz |
|---|---|
| `GET /cidades` | a lista inteira, cada par cidade×alvo com `disponivel` e `motivo` |
| `POST /estimativa` | resolve o modelo; par indisponível responde **422** com o motivo, não 500 |
| `GET /mercado` | uma consulta na tabela de mercado |
| `GET /bairros/indicadores` | mesma tabela, filtro por bairro |
| `GET /bairros`, `GET /opcoes` | do cartão do modelo resolvido |
| `GET /modelo/card` | o cartão inteiro do modelo que respondeu |
| `GET /saude`, `GET /pronto` | prontidão checa a refinada e ao menos um modelo por par disponível |

`GET /cidades` é o que permite a tela desabilitar antes do clique, em vez de descobrir no
erro. A rota existe para a tela não precisar adivinhar.

## A regra que não muda

**O backend importa a definição de feature. Não reimplementa.**

O modelo não consome área e quartos crus: consome `log_area`, `area_por_quarto`,
`banhos_por_quarto` e as flags de ausência. Se o backend recalcular qualquer uma com outro
tratamento de divisão por zero, nada quebra — sai uma previsão plausível e errada, sem
exceção. Por isso a dependência de `engenharia/` é de biblioteca, e nunca de cópia.

## O que ele não faz

- **Não persiste requisição.** Endereço aproximado mais faixa de preço identifica um
  imóvel, e um imóvel tem dono. Se registro de uso for preciso, guarda-se contagem
  agregada, nunca o payload.
- **Não importa o módulo de privacidade.** Ele existe para a coleta, onde o texto do
  anúncio chega com telefone de corretor. Aqui não há texto livre.
