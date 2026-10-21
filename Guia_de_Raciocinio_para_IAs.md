# Shadow Clan RPG — Guia de Raciocínio para IAs

Complemento do **Arquivo Mestre (GDD v1.2)**. O Arquivo Mestre diz *o que* o jogo é. Este guia diz *como pensar* ao trabalhar nele, para que qualquer IA tome as mesmas decisões que eu tomaria. Ele descreve o método e as regras de decisão, não "intuição": tudo aqui pode ser verificado no GDD.

## 1. Leia nesta ordem

1. Arquivo Mestre inteiro (principalmente seções 1, 2, 9, 12, 20, 28, 31).
2. Este guia.
3. `combate_reativo.py` (único código novo já construído).
4. Ignore `win.txt`, `schema.sql` e `combate.py` antigos: são rascunhos obsoletos.

**Autoridade em caso de conflito:** Arquivo Mestre > seção mais nova sobre a mais antiga > código > rascunhos. Legenda: 🔒 fechado pelo dono do jogo · 💡 sugestão calibrável · ⚠️ perguntar, nunca assumir em silêncio.

## 2. O jogo em 6 frases

- RPG ninja por turnos para Telegram Mini App; 5 classes com mecânicas realmente diferentes.
- O mundo pertence aos jogadores: 5 aldeias, Guerra das Vilas semanal, Kage conquistado por desafio.
- Dinheiro real compra só cosmético e conveniência, nunca poder (modelo League of Legends).
- Servidor autoritativo: combate, drops e moeda nunca são calculados no cliente.
- Sessões de 3 a 10 minutos, metas de semanas; sempre há um próximo objetivo visível.
- Uma Saga de 28 dias dá a história; escolhas coletivas mudam o mundo.

## 3. O teste dos 5 pilares (rodar antes de qualquer decisão)

| # | Pergunta | Se falhar |
|---|---|---|
| 1 | Isso dá poder por dinheiro real, direta ou indiretamente (energia, XP, Ryos, atalho em PvP)? | Recusar ou converter em cosmético |
| 2 | O servidor decide tudo que importa? | Mover a lógica para o backend |
| 3 | Cada classe continua diferente e perto de 50% de vitória? | Ajustar coeficiente, nunca preço real |
| 4 | O banco de jogadores continua seguro (migração aditiva, backup)? | Reescrever como migração |
| 5 | Existe um próximo objetivo visível para o jogador? | Adicionar barra, meta ou "falta X" |

Exemplos reais de decisão por esse teste: o VIP antigo com +Ryos e +XP foi descartado (pilar 1); a Poção de Energia só circula entre jogadores, com teto diário (pilar 1); bônus de Ascensão valem só em PvE e a Arena os normaliza (pilar 3).

## 4. Regras de raciocínio que sigo

1. **Dono do jogo decide, IA propõe.** Uma regra 🔒 não muda por conta própria. Se parecer errada, apontar o problema e deixar o dono decidir (ex.: o Kage "inalcançável" virou a regra dos 90%, aprovada por ele).
2. **Ambiguidade vira pergunta ou suposição marcada.** Nunca interpretar em silêncio. Ex.: "Poderão" virou Mapas do Mundo e "Frete" virou Free, ambos registrados como ⚠️ e depois confirmados.
3. **Preferir o conservador.** Na dúvida entre dar mais ou menos poder, dar menos; entre mais ou menos risco para o banco, escolher menos.
4. **Todo ganho tem teto e todo ralo tem motivo.** Fama, energia, treino e expedições têm limite diário para ninguém dominar sozinho. Ryos precisam de ralos (hospital, forja, taxas, viagem) contra inflação.
5. **Anti-snowball e anti-abuso por padrão.** Resistência decrescente a controle, Moral para vila derrotada, escudo de 48 h para o novo Kage, antifraude de duelo combinado.
6. **Prestígio e cosmético, nunca poder.** Tudo ligado ao Kage, Sannins e Sagas é status, história e visual.
7. **Servidor autoritativo e idempotente.** Transações de moeda e item atômicas, com ledger auditável.
8. **Números calibráveis ficam em constantes nomeadas** no topo do arquivo, com `(?)` quando o GDD não definiu o valor. Nunca espalhar número mágico.
9. **Medir antes de afirmar.** Equilíbrio de classes se prova com simulação, não com opinião. Ver seção 6.
10. **Dizer o que não foi feito.** Todo entregável termina listando o que ficou de fora e o que é suposição.

## 5. Árvore de decisão para uma feature nova

```
A feature está no GDD?
├─ Sim → implementar exatamente como está; se 💡, expor valores como constantes.
└─ Não → passa nos 5 pilares?
         ├─ Não → não fazer; propor alternativa cosmética ou social.
         └─ Sim → escolher a opção mais conservadora, marcar ⚠️,
                  registrar na seção 17/31.4 e avisar o dono.
```

## 6. Estado atual (21/10/2026)

**Pronto:** Arquivo Mestre v1.2; `combate_reativo.py` v0.1 (fórmula do Bloco 7, Postura × Reação, 5 classes, 7 ambientes, Narrador).

**Não pronto:** schema.sql, api.py, index.html, árvore de jutsus, dados de aldeias e Zonas, banco de frases do Narrador, reações próprias de cada classe, Bíblia de Mundo (rascunho 💡 na seção 30).

**Primeira simulação (meta 47% a 53%):** Marionetista 77%, Taijutsu 68%, Kunoichi 58%, Ninjutsu 38%, Genjutsu 9%. Isso mostra onde o motor quebra: Genjutsu fraco demais, Marionetista e Taijutsu fortes demais. Não é balanceamento final, pois os bots usam pesos fixos.

## 7. Como estender `combate_reativo.py`

- `python combate_reativo.py demo [Classe1 Classe2 Ambiente]` mostra uma luta narrada e a crônica final.
- `python combate_reativo.py sim [N]` imprime a matriz de vitórias; **rodar sempre depois de mexer em qualquer constante** e anexar o resultado ao PR ou à seção 31.3.
- Constantes calibráveis no topo: `POSTURA`, `REACAO_CERTA`, `CONTRA_ATAQUE`, `GATE_BONUS`, `MARIONETE_PCT`, `SURPRESA`, `STATUS_ELEM`, `AMBIENTES`.
- Para uma classe nova ou mudança de regra: 1) atualizar o GDD, 2) alterar o código, 3) rodar `sim`, 4) registrar a decisão na 31.4.
- O motor não deve importar nada de rede ou banco: ele recebe `Lutador` e devolve resultado e log, para ser chamado pelo `api.py`.

## 8. Pegadinhas já encontradas

- Contagens divergentes em rascunhos antigos: mochila 30 (não 39), guilda 5 (não 4), energia 0–25 (não 100). O GDD vale.
- "Ninja Médico" é o nome antigo; a classe é **Kunoichi**, aberta a qualquer sexo. Equipamento da Kunoichi: bisturis, kunais e túnicas.
- Torre: andares 90 e 180 têm mini-chefe e chefe ao mesmo tempo; **prevalece o chefe**.
- Taijutsu não tem elemento (modificador elemental sempre 1,0) e Ninjutsu tem +50% de vantagem (as outras classes, +25%).
- "Três Kages" significa 3 Sannins por aldeia, dos quais só 1 é o Kage.
- Janela de Batalha: sábado, 20h–22h (Brasília). Desafio ao trono: quem tem 90% da Fama do Kage e 20.000+ de Fama.

## 9. Modelo de prompt para passar a tarefa a outra IA

```
Você vai trabalhar no Shadow Clan RPG. Leia primeiro o Arquivo Mestre (GDD v1.2) e o
Guia de Raciocínio. O GDD vence qualquer outra fonte.
Tarefa: <descreva em uma frase>.
Regras: rode o teste dos 5 pilares; valores calibráveis em constantes nomeadas;
suposições marcadas com ⚠️; no fim, liste o que NÃO foi feito.
Entregue: <arquivos esperados> e o resultado de `python combate_reativo.py sim`
se tocou em combate.
```

## 10. O que ainda depende do dono do jogo

- Aprovar ou ajustar a Bíblia de Mundo (seção 30): clãs, organizações criminosas e Saga 1.
- Decidir se Postura × Reação deve usar 2 reações simultâneas por turno (como implementado) ou outra leitura dos Pontos de Reação.
- Confirmar os bônus dos Portões e a regra do Portão 8 (seção 31.4).
