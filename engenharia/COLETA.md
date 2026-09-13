# Plano de coleta — as 15 cidades

Decidido em 2026-09-13. **Coleta de anúncio acontece em quinze cidades e em mais
nenhuma.**

A lista vive em código, não aqui: [`comum/cidades_alvo.py`](comum/cidades_alvo.py).
Este documento explica por que ela existe e como uma cidade sai de
`a_inventariar` para `coletando`.

---

## Por que fechar a lista

A base tinha anúncio de **41 cidades**. Trinta e seis delas com menos de vinte
linhas: Águas de Lindóia com duas, Pardinho com uma, Brotas de Macaúbas com uma.

| medida | valor |
|---|---|
| cidades com algum anúncio | 41 |
| cidades com 20 anúncios ou mais | 5 |
| cidades com 100 ou mais | 4 |
| cidades com coordenada (CNEFE carregado) | 7 |

Essa cauda não treina modelo, não enche mapa e não vira produto. Custa
requisição no site da fonte, linha no lago e ruído em toda contagem por cidade.

As quinze escolhidas são **as que o índice FipeZAP publica e que o mapa do
produto mostra**. Cobrir exatamente elas alinha três coisas que estavam
desalinhadas: o que coletamos, o que o índice de mercado cobre, e o que a tela
exibe.

## As quinze

Ordenadas por prioridade, e a ordem tem razão: primeiro as que já rendem, depois
as de maior mercado, por último as menores. Coletar Vitória antes do Rio
inverteria o retorno do mesmo esforço.

| # | cidade | UF | IBGE | estado | R$/m² |
|---|---|---|---|---|---|
| 1 | Santos | SP | 3548500 | **coletando** | 8.547 |
| 2 | São Paulo | SP | 3550308 | **coletando** | 12.143 |
| 3 | Guarujá | SP | 3518701 | **coletando** | 7.035 |
| 4 | Rio de Janeiro | RJ | 3304557 | a inventariar | 11.216 |
| 5 | Brasília | DF | 5300108 | a inventariar | 10.369 |
| 6 | Fortaleza | CE | 2304400 | a inventariar | 9.502 |
| 7 | Goiânia | GO | 5208707 | a inventariar | 8.482 |
| 8 | Florianópolis | SC | 4205407 | a inventariar | 13.531 |
| 9 | Balneário Camboriú | SC | 4202008 | a inventariar | 15.343 |
| 10 | Itajaí | SC | 4208203 | a inventariar | 13.354 |
| 11 | Itapema | SC | 4208302 | a inventariar | 15.403 |
| 12 | Vitória | ES | 3205309 | a inventariar | 15.650 |
| 13 | Campo Grande | MS | 5002704 | a inventariar | 6.830 |
| 14 | São Luís | MA | 2111300 | a inventariar | 8.829 |
| 15 | Aracaju | SE | 2800308 | a inventariar | 5.826 |

**Os códigos IBGE foram conferidos na API de localidades do IBGE em 2026-09-13**,
não escritos de memória. Eles são a chave do CNEFE, e um código errado baixa o
município errado sem erro nenhum: a tabela sai cheia, com CEPs de outra cidade.

## A regra é código, não recomendação

`comum.cidades_alvo.esta_no_alvo()` é o predicado que o coletor consulta **antes
de gravar**. Anúncio de cidade fora da lista é descartado na ingestão.

Descartar depois, no tratamento, já teria custado requisição no site da fonte e
espaço no lago — e a regra teria virado sugestão, que é o que acontece com regra
que só existe em documento.

**Uma coisa que a regra não impede, de propósito.** Plataforma regional devolve
imóvel de cidade vizinha: o Guarujá entrou pela coleta apontada para Santos.
Enquanto a cidade estiver na lista, o anúncio fica. O filtro é por cidade-alvo,
não por cidade que pedimos.

---

## Como uma cidade entra

Quatro etapas, na ordem, e nenhuma pode ser pulada.

### 1. Inventário — antes de qualquer linha de adapter

Skill `inventariar-fonte`. O que ele tem de produzir para a cidade:

- `robots.txt` de cada domínio candidato. **Domínio que nos proíbe é descartado,
  nunca contornado**, e vira alvo de parceria em vez de alvo de coleta.
- A matriz atributo × plataforma, comparando **o conjunto de campos da busca com
  o do detalhe**. Uma API de busca com 37 campos escondia uma de detalhe com
  214; outra plataforma tinha 151 na busca e 152 no detalhe. Sem comparar, não
  se sabe em qual caso se está.
- O painel de rede inspecionado **na busca E no detalhe, separadamente**. As duas
  APIs deste projeto só apareceram assim.
- n ≥ 10 páginas para qualquer conclusão estrutural, **com o n declarado**.
  Conclusões erradas neste projeto vieram de n=3.

### 2. CNEFE do município

`python -m coleta.cnefe.coletar --municipios {cod_ibge} --write`

Sem isso a cidade não tem coordenada, e bairro sem coordenada fica fora do mapa.
O código vem de `cidades_alvo`, não digitado à mão.

### 3. Adapter

Skill `novo-adapter`, depois do inventário aprovado. O que o checklist cobra:

- **A lista não é a fonte. O anúncio é.** Condomínio, IPTU, andar, ano, vista e
  descrição vivem na página do imóvel individual.
- Onde a fonte oferece dado estruturado — `@type`, `floorSize`, campo de API —
  ele tem precedência. Regex sobre texto livre é fallback, nunca fonte primária.
- **Teste do caso em que a fonte responde bem e o dado vem errado.** É o modo de
  falha caro: palavra de busca errada devolvendo `200` com zero resultados, POST
  virando GET num redirect, caminho de priming que responde 500. Nenhum levanta
  exceção; todos produzem coleta limpa e incompleta.

### 4. Primeira coleta e auditoria

```bash
python -m coleta.anuncios.coletar --config config/fontes.yaml \
    --cidade {slug} --limit 20 --write
python painel.py
```

`--limit 20` na primeira vez, sempre. Coleta cheia contra adapter novo gasta a
paciência do site da fonte para descobrir um erro que vinte linhas mostrariam.

Depois, skill `auditar-coleta`: relê os parquets do disco e verifica allowlist,
PII, faixas implausíveis, cobertura por campo, cardinalidade e duplicatas.

A cidade passa a `coletando` em `cidades_alvo.py` **depois** da auditoria limpa,
e não depois da primeira coleta que não quebrou.

---

## O que muda no CNEFE

Hoje o coletor carrega sete municípios, e cinco deles não estão na lista:
Cubatão, Bertioga, Praia Grande e São Vicente entraram porque são vizinhos de
Santos e apareciam na base.

**Eles ficam.** O CNEFE já está em disco, custa nada manter, e a geocodificação
por logradouro melhora quando a tabela cobre municípios vizinhos — endereço de
divisa casa em mais de um.

O que muda é o inverso: os **doze municípios que faltam** entram conforme a
cidade sai de `a_inventariar`. Baixar os doze agora seria carregar ~1 GB de
endereço para cidade que ainda não tem um anúncio coletado.

---

## O painel

```bash
python painel.py
```

HTML local, administrativo, **não vai para a nuvem**. Mostra as quinze cidades
com quantos anúncios de venda e de locação cada uma tem, a saúde de cada camada
da esteira, e o que cada plataforma trouxe.

Cidade sem anúncio aparece apagada e não sumida: o que falta coletar é
informação de operação tanto quanto o que já foi.

---

## Documentos irmãos

| arquivo | papel |
|---|---|
| [`comum/cidades_alvo.py`](comum/cidades_alvo.py) | a lista, em objetos, e o predicado que o coletor consulta |
| [`PROCESSOS.md`](PROCESSOS.md) | os 17 processos e o mapa de migração |
| [`README.md`](README.md) | as camadas e as regras invioláveis |
