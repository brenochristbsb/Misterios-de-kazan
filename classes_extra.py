import logging
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import atributos5
import classes_bloco3 as b3
from api import get_db, get_player, rank_titulo, effective_stats
from jutsus_dados import QUIZ, elemento_do_teste
from game_extra import BOLSA_MAX

router = APIRouter()
log = logging.getLogger("classes_extra")

CUSTO_RESET_CLASSE = 50   
CUSTO_SLOT_PET = 100      
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
    respostas: list[int] = Field(default_factory=list, max_length=len(QUIZ))  

class SlotReq(BaseModel):
    telegram_id: int = Field(gt=0)

class ModuloReq(BaseModel):
    telegram_id: int = Field(gt=0)
    modulo: str = Field(min_length=1, max_length=20)
    acao: Literal["instalar", "remover"]

class EspecReq(BaseModel):
    telegram_id: int = Field(gt=0)
    ramo: Literal["armadilhas", "combate", "elementais"]

class PassivaReq(BaseModel):
    telegram_id: int = Field(gt=0)
    opcao: Literal["hospital", "tratamento"]

def _erro(fn, *a):
    try:
        return fn(*a)
    except b3.RegraErro as e:
        raise HTTPException(400, str(e))

def _livre(p) -> None:
    if p.luta or p.masmorra or p.torre_sessao:
        raise HTTPException(409, "Isso só pode ser feito fora de combate.")

def _slots(p) -> int:
    return max(PET_SLOTS_INICIAIS, p.pet_slots or 0)

# ── Rotas de Informação e Escolha de Classe ──
@router.get("/api/player/{telegram_id}/classes")
def classes_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    cl = atributos5.classe_efetiva(p)
    out = {
        "atual": p.classe,
        "ouro_ninja": p.ouro_ninja,
        "custo_reset": CUSTO_RESET_CLASSE,
        "pet_slots": {"atual": _slots(p), "max": PET_SLOTS_MAX, "custo": CUSTO_SLOT_PET},
        "classes": [{"id": k, "nome": r["nome"], "principal": r["principal"], "secundario": r["secundario"],
                     "recurso": "furia" if k == "taijutsu" else "chakra", "equip": list(r["equip"]),
                     "descricao": DESCRICAO[k]} for k, r in b3.CLASSES.items()],
    }
    if cl == "marionetista":
        m = b3.normalizar(getattr(p, "marionete", None), atributos5.universais(effective_stats(p)))
        out["marionete"] = {**m, "defesa": b3.defesa_marionete(atributos5.universais(effective_stats(p)), m["espec"]),
                            "pontos_livres": b3.pontos_espec_livres(p.level, m["espec"]), "instalar_ryos": b3.MAR_INSTALAR_RYOS,
                            "catalogo": {k: {"nome": v["nome"], "custo": b3.custo_modulo(k, m["espec"]), "recarga": b3.recarga_modulo(k, m["espec"]),
                                             "elem": v["elem"]} for k, v in b3.MODULOS.items()}}
    if cl == "ninja_medico":
        out["passiva_medica"] = getattr(p, "passiva_medica", "") or None
        out["opcoes_passiva"] = {"hospital": "Cura gratuita do hospital a cada 5 min (em vez de 10)", "tratamento": "-30% no custo dos tratamentos do hospital"}
    return out

@router.post("/api/classe/escolher")
def classe_escolher(payload: ClasseReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.classe:
        raise HTTPException(409, "Você já escolheu uma classe. Para trocar, use o reset de classe no Dojo.")
    p.classe = payload.classe
    db.commit()
    return {"mensagem": f"Você agora é {b3.CLASSES[p.classe]['nome']}.", "classe": p.classe}

@router.post("/api/classe/resetar")
def classe_resetar(payload: ClasseReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.classe:
        raise HTTPException(409, "Você ainda não tem classe: escolha uma (grátis).")
    if payload.classe == p.classe:
        raise HTTPException(400, "Você já é desta classe.")
    if p.luta or p.masmorra or p.torre_sessao:
        raise HTTPException(409, "Termine a luta ou a masmorra antes de resetar sua classe.")
    novo_elem = None
    if payload.classe != "taijutsu" and not p.elemento:
        if len(payload.respostas) != len(QUIZ) or any(not 0 <= r <= 4 for r in payload.respostas):
            raise HTTPException(422, "Responda o teste do elemento para sair do Taijutsu.")
        novo_elem = elemento_do_teste(payload.respostas)
    equipados = [i for i in p.inventory if i.is_equipped]
    if equipados and len(p.inventory) >= BOLSA_MAX:
        raise HTTPException(409, "Limpe sua mochila antes de resetar sua classe")
    if (p.ouro_ninja or 0) < CUSTO_RESET_CLASSE:
        raise HTTPException(402, f"Ouro Ninja insuficiente: o reset custa {CUSTO_RESET_CLASSE}.")

    for it in equipados:  
        it.is_equipped = False
    antiga, p.classe = p.classe, payload.classe
    if payload.classe == "taijutsu":  
        p.elemento, p.elementos_extra = "", []
    elif novo_elem:
        p.elemento = novo_elem
    p.ouro_ninja -= CUSTO_RESET_CLASSE
    db.commit()
    log.info("reset_classe player=%s %s->%s custo=%s", p.id, antiga, p.classe, CUSTO_RESET_CLASSE)
    return {"mensagem": f"Classe alterada para {b3.CLASSES[p.classe]['nome']}. {len(equipados)} item(ns) voltaram para a mochila.",
            "classe": p.classe, "desequipados": len(equipados), "ouro_ninja": p.ouro_ninja}

# ── Rotas de Módulos, Especialização e Passiva Médica ──
@router.post("/api/marionete/modulo")
def marionete_modulo(payload: ModuloReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if atributos5.classe_efetiva(p) != "marionetista":
        raise HTTPException(403, "Só o Marionetista tem marionete.")
    _livre(p)
    m = b3.normalizar(getattr(p, "marionete", None), atributos5.universais(effective_stats(p)))
    if payload.acao == "instalar":
        m, custo = _erro(b3.instalar_modulo, m, payload.modulo, p.ryos)
        p.ryos -= custo  
    else:
        m = _erro(b3.remover_modulo, m, payload.modulo)
    p.marionete = m
    db.commit()
    return {"marionete": m}

@router.post("/api/marionete/especializar")
def marionete_especializar(payload: EspecReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if atributos5.classe_efetiva(p) != "marionetista":
        raise HTTPException(403, "Só o Marionetista tem especializações.")
    _livre(p)
    m = b3.normalizar(getattr(p, "marionete", None), atributos5.universais(effective_stats(p)))
    m = _erro(b3.investir_espec, m, payload.ramo, p.level)
    m = b3.normalizar(m, atributos5.universais(effective_stats(p)))  
    p.marionete = m
    db.commit()
    return {"marionete": m, "pontos_livres": b3.pontos_espec_livres(p.level, m["espec"])}

@router.post("/api/medico/passiva")
def medico_passiva(payload: PassivaReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    p.passiva_medica = _erro(b3.escolher_passiva, atributos5.classe_efetiva(p), getattr(p, "passiva_medica", ""), payload.opcao)
    db.commit()
    return {"passiva_medica": p.passiva_medica}

@router.post("/api/pets/slot/comprar")
def pet_slot_comprar(payload: SlotReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    atual = _slots(p)
    if aktual >= PET_SLOTS_MAX:
        raise HTTPException(409, f"Você já tem o máximo de {PET_SLOTS_MAX} slots de pet.")
    if (p.ouro_ninja or 0) < CUSTO_SLOT_PET:
        raise HTTPException(402, f"Ouro Ninja insuficiente: cada slot custa {CUSTO_SLOT_PET}.")
    p.ouro_ninja -= CUSTO_SLOT_PET
    p.pet_slots = atual + 1
    db.commit()
    log.info("pet_slot player=%s slots=%s", p.id, p.pet_slots)
    return {"mensagem": f"Slot de pet liberado ({p.pet_slots}/{PET_SLOTS_MAX}).", "pet_slots": p.pet_slots, "ouro_ninja": p.ouro_ninja}
