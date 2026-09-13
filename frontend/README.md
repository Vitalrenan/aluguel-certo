# frontend

As telas. Mantidas como estão no `frontend/` da raiz: as rotas do Next, os hooks e os
componentes não mudam de forma.

Esta pasta existe para receber **a única mudança que D4 obriga**, e nada além dela.

---

## A mudança: cidade sem modelo

Decidido em 2026-09-12. Hoje só Santos e São Paulo têm modelo, e a tela precisa dizer isso
em vez de errar.

**A cidade continua na lista.** Escondê-la faria o usuário concluir que não atendemos o
lugar. Mostrá-la desabilitada faz ele saber que ainda não atendemos, o que é a verdade.

Ao escolher uma cidade sem modelo, a tela mostra **modelo estatístico ainda não
disponível** — o texto vem do backend, em `motivo`, e não é escrito na tela. Um texto
duplicado nos dois lados diverge na primeira vez que alguém mudar um só.

## De onde a tela sabe

`GET /cidades` devolve a lista inteira com `disponivel` e `motivo` por par cidade×alvo.
A tela desabilita **antes do clique**, em vez de descobrir no erro da estimativa.

## A disponibilidade é por par, não por cidade

Este é o detalhe que a implementação erra se ninguém avisar: **São Paulo tem modelo de
venda e não tem de locação.** Medido em 2026-09-12 — 5.628 linhas de venda e 2 de locação.

Uma tela que trate "São Paulo" como disponível ou indisponível inteira vai oferecer aluguel
em São Paulo e receber 422. O seletor de transação depende do seletor de cidade.

| cidade | venda | locação |
|---|---|---|
| Santos | disponível | disponível |
| São Paulo | disponível | **indisponível** |
| demais | indisponível | indisponível |

## O que mais chega, e é opcional exibir

As respostas passam a carregar um bloco de proveniência: qual cidade respondeu, de qual mês
é o modelo, e o n de treino. É informação disponível, não obrigação de tela — a decisão de
mostrar é de quem desenha.

## O que não muda

Nenhuma rota muda de forma, nenhum contrato de campo é quebrado, e o design system de
`frontend/app/globals.css` continua sendo a fonte única: uma família de acento só, azul,
duas sombras, e o sentido carregado pelo rótulo, nunca pela matiz.
