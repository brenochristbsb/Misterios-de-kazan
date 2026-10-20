"""
boneco_extra.py — Boneco de treino: um alvo de testes para medir o seu dano.

Regras (de propósito, nada disso vale no boneco):
    - não dá XP, ryos, drops, fama, moral nem progresso de missão, conquista ou passe;
    - não aumenta atributos nem status (nada passa por fim_luta/apply_exp);
    - não gasta energia, vida, chakra nem itens, e o boneco nunca ataca;
    - não mexe nos status negativos que o jogador carrega para a próxima luta.
O custo de chakra de cada técnica aparece normalmente na tela, mas aqui ele não é cobrado.

A luta fica em Player.luta com tipo "boneco" e o placar do treino em L["tr"]. O game_extra.luta_agir entrega
essas lutas para agir_boneco() (import dentro da função, para não haver import circular).
"""
import copy
import random
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api import ATTRS, get_db, get_player
from game_extra import (BASICO, ELEMENTOS, JUTSUS, DIFICULDADES, _linha, _me, custo_tecnica, golpe, luta_view, mod_elem,
                        stats_inimigo)

router = APIRouter()
BONECO_HP = 5000
NIVEIS = [1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
DIFS = ["fácil", "normal", "difícil", "chefe"]


class BonecoReq(BaseModel):
    telegram_id: int = Field(gt=0)
    nivel: int = Field(ge=1, le=100)
    dif: Literal["fácil", "normal", "difícil", "chefe"] = "normal"
    elemento: Literal["fogo", "agua", "vento", "raio", "terra"] = "fogo"


@router.get("/api/boneco/opcoes")
def opcoes():
    return {"niveis": NIVEIS, "difs": DIFS, "elementos": list(ELEMENTOS), "hp": BONECO_HP}


@router.post("/api/boneco/iniciar")
def iniciar(payload: BonecoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if p.masmorra or p.torre_sessao:
        raise HTTPException(409, "Termine a masmorra ou a Torre antes de treinar.")
    stats, _, _ = stats_inimigo(p, payload.nivel, payload.dif)
    stats = {**stats, "agilidade": 0}  # o boneco nunca esquiva: o dano medido é o dano real
    p.luta = {"tipo": "boneco", "nome": "Boneco de Treino", "boss": False, "stats": stats, "hp": BONECO_HP, "hp_max": BONECO_HP,
              "chakra": 0, "turno": 0, "defendendo": False, "atordoado": False, "me_atordoado": False, "pet": None,
              "pet_id": None, "meu_elem": p.elemento, "elemento": payload.elemento, "st": {}, "escudo": 0, "reserva": 0,
              "nivel": payload.nivel, "dif": payload.dif, "log": [f"Um boneco de treino (nível {payload.nivel}, {payload.dif}, elemento {payload.elemento}) está à sua frente. Ele não ataca."],
              "tr": {"golpes": 0, "dano": 0, "maior": 0, "crits": 0, "quebras": 0}}
    db.commit()
    return {"luta": luta_view(p)}


def _resumo(L: dict) -> dict:
    t = L["tr"]
    return {"vitoria": None, "tipo": "boneco", "inimigo": L["nome"], "nivel": L.get("nivel"), "dif": L.get("dif"), "elemento": L.get("elemento"),
            "log": L["log"][-6:], "exp": 0, "ryos": 0, "drops": [], "extras": [], "novo_pet": None, "niveis_ganhos": 0, "xp_reduzido": False,
            "missao": None, "on_mission": False, "chefe": False, "moral": None, "mapa": None,
            "treino": {**t, "media": round(t["dano"] / t["golpes"], 1) if t["golpes"] else 0}}


def agir_boneco(db: Session, p, payload):
    L, acao = copy.deepcopy(p.luta), payload.acao
    if acao == "fugir":  # "Encerrar treino": termina sem custo nem penalidade
        p.luta = None
        db.commit()
        return {"luta": None, "resultado": _resumo(L)}
    if acao == "pocao":
        raise HTTPException(400, "No boneco de treino não se usam itens: nada é gasto aqui.")
    me = _me(p, L)
    L["turno"] += 1
    if acao == "defender":
        L["log"].append("Você respira fundo. O boneco não ataca, então não há o que aparar.")
    else:
        poder, crit_extra, base, nome_golpe, el, custo = 1.0, 0.0, acao, BASICO["fisico"], None, 0
        if acao == "jutsu":
            j = JUTSUS.get(payload.item or "")
            if j is None or payload.item not in (p.jutsus or {}).values():
                raise HTTPException(400, "Você ainda não aprendeu esse jutsu.")
            base = {"ninjutsu": "ninjutsu", "genjutsu": "genjutsu", "taijutsu": "fisico"}[j["cat"]]
            poder, el, nome_golpe = j["poder"], j["elem"], j["nome"]
            crit_extra = 0.15 if j["cat"] == "taijutsu" else 0.0
        elif acao == "ninjutsu":
            el, nome_golpe = p.elemento, BASICO["ninjutsu"][p.elemento]
        elif acao == "genjutsu":
            nome_golpe = BASICO["genjutsu"]
        elif acao == "secreta":
            if not p.tecnica:
                raise HTTPException(400, "Você ainda não domina uma técnica secreta.")
            nome_golpe = p.tecnica
        mod = mod_elem(el, L.get("elemento"))
        dmg, crit, _ = golpe(me, L["stats"], base, poder, mod, crit_extra)
        nota = " Vantagem elemental!" if dmg and mod > 1 else " O elemento do boneco resiste." if dmg and mod < 1 else ""
        L["log"].append(_linha("Você", base, dmg, crit, nome_golpe) + nota)
        t = L["tr"]
        t["golpes"] += 1
        t["dano"] += dmg
        t["maior"] = max(t["maior"], dmg)
        t["crits"] += int(bool(crit))
        L["hp"] -= dmg
        if L["hp"] <= 0:  # o boneco se recompõe: dá para continuar testando para sempre
            L["hp"] = L["hp_max"]
            t["quebras"] += 1
            L["log"].append("O boneco foi destruído e se recompôs.")
    L["log"] = L["log"][-60:]
    p.luta = L
    db.commit()
    return {"luta": luta_view(p), "resultado": None}
