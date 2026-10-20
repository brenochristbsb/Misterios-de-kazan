# Bloco 3 — Marionetista e Ninja Médico

## Como integrar (5 passos)
1. Copie para a pasta do backend: `classes_bloco3.py` e `bloco3_extra.py` (novos). `classes_extra.py` já existe no seu projeto e **não** é tocado.
2. Substitua `game_extra.py` (no envio estava `game_extra-1.py`), `atributos5.py` e `api.py` pelos alterados, ou aplique `diffs/*.diff` se o seu repositório já mudou.
3. Em `models.py`, adicione as 2 colunas (`models_patch.txt`). O `ensure_columns()` cria no próximo start; ou rode `migrations/2026_10_20_bloco3_classes.sql` (troque o nome da tabela).
4. Atualize `index.html` (textos das classes + barras de marionete/veneno no combate).
5. Rode os testes: `pytest tests/ -q` (ver nota abaixo).

## O que cada peça faz
- `classes_bloco3.py`: todas as regras e números (puro, sem banco). O cliente nunca envia custo/cura/dano.
- `game_extra.py`: marionete absorvendo no `turno_inimigo`; ações novas em `/api/luta/agir`: `invocar`, `agulhas`, `veneno`, `modulo`, `cortar_linhas`, `curar` (ação livre), `bisturi`; hospital com as passivas; descanso/hospital consertam a marionete.
- `atributos5.py`: `dano_verdadeiro` explícito no golpe; Marionetista com bônus elemental comum (1.25), sem o do Ninjutsu.
- `bloco3_extra.py`: `GET /api/player/{id}/bloco3`, `POST /api/marionete/modulo`, `/api/marionete/especializar`, `/api/medico/passiva`.

## Decisões de balanceamento (ajuste em `classes_bloco3.py`)
- Marionete: HP = 40 + 4·INT + 5·VIG (+6 por ponto de Combate). Invocar 20 chakra, manutenção 2/turno. Destruída: só volta com descanso (100%) ou hospital (gratuito 50% / pago 100%).
- Dano que passa do HP restante da marionete vaza para o jogador.
- Contorno: chefes têm ~33% de ataques que perfuram ou cortam as linhas (2 turnos sem proteção); rivais de PvP ~30%. No PvP o jogador pode usar "Cortar linhas"; a técnica secreta perfura.
- Veneno: até 5 acúmulos, 3 turnos, resistência por Vigor (chefes +15%), tick limitado a 6% da vida máxima, chefes às vezes se tratam.
- Módulos: 8 (lâminas, armadilha, fumaça + 5 elementais), 3 slots, 150 ryos para instalar, recarga por módulo. Especialização: 1 ponto a cada 4 níveis, máx. 5 por ramo e 8 no total (não dá para maximizar os 3).
- Cura: ação livre, 1 por turno, 6 por luta, custo sobe +10 de chakra a cada uso; Selo Regenerador tem recarga de 4 turnos. Não passa o turno nem o inimigo age.
- Bisturi: dano verdadeiro (sem Vigor, sem redução duplicada), teto de 25% da vida máxima do alvo por golpe; esquiva continua valendo; a marionete do alvo ainda absorve.
- Passiva médica: escolha única e persistida (Hospital: cura gratuita a cada 5 min; Tratamento: −30% nos preços do hospital).

## ⚠️ Mudança de comportamento
Antes, **todo** ataque do Ninja Médico ignorava o Vigor (regra em `combate.CLASSES`). Pelo texto do Bloco 3, agora só o Bisturi. Para voltar ao antigo: `MEDICO_BASICO_VERDADEIRO = True` em `atributos5.py`.

## O que NÃO foi possível verificar
`combate.py`, `combate_estado.py`, `models.py`, `boneco_extra.py` e outros não vieram no envio.
- `tests/test_classes_bloco3.py` (31 testes) roda sem dependências do jogo.
- `test_atributos5_bloco3.py` e `test_integracao_bloco3.py` usam **stubs** de FastAPI/SQLAlchemy/models/combate; passam (5 e 15), mas validam a lógica, não o banco real. Se o `CLASSES` real tiver outro formato, ajuste os stubs.
- Não achei sistema de passivas existente nos arquivos enviados; se houver em outro módulo, mova a escolha para ele.
- Tela de gerenciamento (instalar módulos, gastar pontos, escolher passiva) **não foi feita no front**: só a API e o combate.
- O boneco de treino ignora as ações novas de propósito (não conheço `boneco_extra.py`).

---
## Atualização: restrições de equipamento e masmorras

**Equipamento por classe** (`classes_bloco3.RESTRICOES_EQUIP`, validado em `/api/inventory/equip`): Taijutsu = manoplas/armadura pesada/couraça; Ninjutsu = katanas/mantos; Genjutsu = leques/flautas/traje de couro; Marionetista = núcleos/madeira; Ninja Médico = bisturis/túnicas. A família sai do **nome** do item; item sem palavra-chave (anéis, colares, botas, luvas, "Armadura de X") continua livre. Ao iniciar uma luta, equipados incompatíveis (ex.: após trocar de classe) são desequipados (`desequipar_incompativeis`; chame também no código de troca de classe, `classes_extra.py`).
⚠️ Itens existentes agora travados: "Manto de Cristal/das Dunas/de Seda Divina/Reforçado" (só Ninjutsu), "Manoplas…" (só Taijutsu), "Couraça de Couro" (só Taijutsu). Faltam itens de loja/forja/drop para as outras classes (núcleos, bisturis, túnicas, leques…): é preciso criá-los com essas palavras no nome.

**Masmorras**: já funcionava assim no código: os 3 fragmentos saem na entrada, e fugir, morrer ou abandonar zera `p.masmorra` (não há como continuar nem repetir a instância). Mudei só as mensagens (agora dizem que o jogador foi expulso para a Vila e que os fragmentos foram perdidos) e cobri com testes.

**Ainda não implementado — "Perfeito (Lendário)"**: depende de definição (ver pergunta na conversa).
