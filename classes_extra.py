"""
classes_extra.py — Escolha e reset de classe (Blocos 1 e 3) e compra de slots de pet (Bloco 1).

Registrado no final do api.py (app.include_router). Usa as colunas que o models.py já tem: Player.classe,
Player.ouro_ninja e Player.pet_slots (o ensure_columns cria tudo sozinho em bancos existentes).

Rotas:
    GET  /api/player/{telegram_id}/classes   -> as 5 classes, a do jogador, ouro e custos
    POST /api/classe/escolher                -> primeira escolha (grátis, só quem ainda não tem classe)
    POST /api/classe/resetar                 -> troca de classe no Dojo por Ouro Ninja
    POST /api/pets/slot/comprar              -> +1 slot de pet por 100 Ouro Ninja (2 iniciais, máximo 20)

Reset de classe (Bloco 1): desequipa TODOS os itens. Com a mochila cheia (30/30) a troca é bloqueada com o aviso
"Limpe sua mochila antes de resetar sua classe". Atributos NÃO são zerados: o jogo atual não tem reserva de pontos
livres para devolver, então só a classe muda (e, com ela, o atributo principal do dano e as regras do Bloco 3).
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api import get_db, get_player
from combate import CLASSES
from jutsus_dados import QUIZ, elemento_do_teste
from game_extra import BOLSA_MAX

router = APIRouter()
log = logging.getLogger("classes_extra")

CUSTO_RESET_CLASSE = 50   # Ouro Ninja (o roteiro não dá o valor: ajuste aqui)
CUSTO_SLOT_PET = 100      # Ouro Ninja por slot extra
PET_SLOTS_INICIAIS, PET_SLOTS_MAX = 2, 20

DESCRICAO = {
    "taijutsu": "Combate corpo a corpo. Sem elementos; os Portões Internos dobram força e agilidade.",
    "ninjutsu": "Mestre elemental: +50% de dano ao atingir a vulnerabilidade do alvo.",
    "genjutsu": "Controle tático: dano baixo, mas atordoa, confunde e drena chakra.",
    "marionetista": "Tanque estrategista: a marionete absorve todo o dano antes de você.",
    "ninja_medico": "Suporte imortal: curas como ação livre e golpes que ignoram a defesa.",
}
ClasseId = Literal["taijutsu", "ninjutsu", "genjutsu", "marionetista", "ninja_medico"]


class ClasseReq(BaseModel):
    telegram_id: int = Field(gt=0)
    classe: ClasseId
    respostas: list[int] = Field(default_factory=list, max_length=len(QUIZ))  # teste do elemento, só para quem sai do Taijutsu


class SlotReq(BaseModel):
    telegram_id: int = Field(gt=0)


def _slots(p) -> int:
    return max(PET_SLOTS_INICIAIS, p.pet_slots or 0)


@router.get("/api/player/{telegram_id}/classes")
def classes_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    return {
        "atual": p.classe,
        "ouro_ninja": p.ouro_ninja,
        "custo_reset": CUSTO_RESET_CLASSE,
        "pet_slots": {"atual": _slots(p), "max": PET_SLOTS_MAX, "custo": CUSTO_SLOT_PET},
        "classes": [{"id": k, "nome": r["nome"], "principal": r["principal"], "secundario": r["secundario"],
                     "recurso": "furia" if k == "taijutsu" else "chakra", "equip": list(r["equip"]),
                     "descricao": DESCRICAO[k]} for k, r in CLASSES.items()],
    }


@router.post("/api/classe/escolher")
def classe_escolher(payload: ClasseReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.classe:
        raise HTTPException(409, "Você já escolheu uma classe. Para trocar, use o reset de classe no Dojo.")
    p.classe = payload.classe
    db.commit()
    return {"mensagem": f"Você agora é {CLASSES[p.classe]['nome']}.", "classe": p.classe}


@router.post("/api/classe/resetar")
def classe_resetar(payload: ClasseReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.classe:
        raise HTTPException(409, "Você ainda não tem classe: escolha uma (grátis).")
    if payload.classe == p.classe:
        raise HTTPException(400, "Você já é desta classe.")
    if p.luta or p.masmorra:
        raise HTTPException(409, "Termine a luta ou a masmorra antes de resetar sua classe.")
    # Taijutsu não tem elemento. Quem sai do Taijutsu (elemento vazio) precisa fazer o teste do elemento
    novo_elem = None
    if payload.classe != "taijutsu" and not p.elemento:
        if len(payload.respostas) != len(QUIZ) or any(not 0 <= r <= 4 for r in payload.respostas):
            raise HTTPException(422, "Responda o teste do elemento para sair do Taijutsu.")
        novo_elem = elemento_do_teste(payload.respostas)
    equipados = [i for i in p.inventory if i.is_equipped]
    # Bloco 1: com a mochila cheia (30/30) a transação é bloqueada, antes de cobrar qualquer coisa
    if equipados and len(p.inventory) >= BOLSA_MAX:
        raise HTTPException(409, "Limpe sua mochila antes de resetar sua classe")
    if (p.ouro_ninja or 0) < CUSTO_RESET_CLASSE:
        raise HTTPException(402, f"Ouro Ninja insuficiente: o reset custa {CUSTO_RESET_CLASSE}.")

    for it in equipados:  # tudo volta para a mochila
        it.is_equipped = False
    antiga, p.classe = p.classe, payload.classe
    if payload.classe == "taijutsu":  # Taijutsu abre mão do elemento e dos elementos extras
        p.elemento, p.elementos_extra = "", []
    elif novo_elem:
        p.elemento = novo_elem
    p.ouro_ninja -= CUSTO_RESET_CLASSE
    db.commit()
    log.info("reset_classe player=%s %s->%s custo=%s", p.id, antiga, p.classe, CUSTO_RESET_CLASSE)
    return {"mensagem": f"Classe alterada para {CLASSES[p.classe]['nome']}. {len(equipados)} item(ns) voltaram para a mochila.",
            "classe": p.classe, "desequipados": len(equipados), "ouro_ninja": p.ouro_ninja}


@router.post("/api/pets/slot/comprar")
def pet_slot_comprar(payload: SlotReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    atual = _slots(p)
    if atual >= PET_SLOTS_MAX:
        raise HTTPException(409, f"Você já tem o máximo de {PET_SLOTS_MAX} slots de pet.")
    if (p.ouro_ninja or 0) < CUSTO_SLOT_PET:
        raise HTTPException(402, f"Ouro Ninja insuficiente: cada slot custa {CUSTO_SLOT_PET}.")
    p.ouro_ninja -= CUSTO_SLOT_PET
    p.pet_slots = atual + 1
    db.commit()
    log.info("pet_slot player=%s slots=%s", p.id, p.pet_slots)
    return {"mensagem": f"Slot de pet liberado ({p.pet_slots}/{PET_SLOTS_MAX}).", "pet_slots": p.pet_slots, "ouro_ninja": p.ouro_ninja}
