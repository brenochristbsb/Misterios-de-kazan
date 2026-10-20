"""
bloco3_extra.py — Endpoints do Bloco 3: marionete (módulos/especialização) e passiva do Ninja Médico.

Registrado no api.py (app.include_router(bloco3_router)). As regras vivem em classes_bloco3.py;
o combate (absorção, veneno, cura livre, bisturi) está em game_extra.py.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import atributos5
import classes_bloco3 as b3
from api import effective_stats, get_db, get_player

router = APIRouter()


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


@router.get("/api/player/{telegram_id}/bloco3")
def bloco3_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    cl, out = atributos5.classe_efetiva(p), {"classe": atributos5.classe_efetiva(p)}
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


@router.post("/api/marionete/modulo")
def marionete_modulo(payload: ModuloReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if atributos5.classe_efetiva(p) != "marionetista":
        raise HTTPException(403, "Só o Marionetista tem marionete.")
    _livre(p)
    m = b3.normalizar(getattr(p, "marionete", None), atributos5.universais(effective_stats(p)))
    if payload.acao == "instalar":
        m, custo = _erro(b3.instalar_modulo, m, payload.modulo, p.ryos)
        p.ryos -= custo  # custo vem do servidor, nunca do cliente
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
    m = b3.normalizar(m, atributos5.universais(effective_stats(p)))  # recalcula o teto de HP
    p.marionete = m
    db.commit()
    return {"marionete": m, "pontos_livres": b3.pontos_espec_livres(p.level, m["espec"])}


@router.post("/api/medico/passiva")
def medico_passiva(payload: PassivaReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    p.passiva_medica = _erro(b3.escolher_passiva, atributos5.classe_efetiva(p), getattr(p, "passiva_medica", ""), payload.opcao)
    db.commit()
    return {"passiva_medica": p.passiva_medica}
