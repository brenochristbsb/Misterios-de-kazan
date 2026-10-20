"""
vip_passe.py — Bloco 6: VIP Shinobi/Shogun e Passe de Temporada.
"""
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from api import get_db, get_player

router = APIRouter()

PLANOS = {
    "shinobi": {"custo": 150, "dias": 30, "bonus_ryos": 0.15, "bonus_xp": 0.0, "slots": 40, "cooldown": 7},
    "shogun": {"custo": 300, "dias": 30, "bonus_ryos": 0.35, "bonus_xp": 0.15, "slots": 60, "cooldown": 5, "isento_taxa": True, "macros": True},
}

def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normaliza para datetime com fuso UTC (a coluna TIMESTAMP volta 'naive')."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass
class VipStatus:
    plano: Optional[str]
    expira_em: Optional[datetime]
    ativo: bool

    @property
    def config(self):
        return PLANOS.get(self.plano) if self.plano else None


def vip_ativo(player) -> VipStatus:
    if not player.vip_plano or not player.vip_expira_em:
        return VipStatus(None, None, False)
    expira = _utc(player.vip_expira_em)
    ativo = expira > datetime.now(timezone.utc)
    return VipStatus(player.vip_plano if ativo else None, expira, ativo)


def multiplicador_ryos(player) -> float:
    v = vip_ativo(player)
    return 1.0 + (v.config["bonus_ryos"] if v.config else 0.0)


def multiplicador_xp(player) -> float:
    v = vip_ativo(player)
    return 1.0 + (v.config["bonus_xp"] if v.config else 0.0)


class ComprarVIPRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    plano: str


@router.post("/api/vip/comprar")
def comprar_vip(payload: ComprarVIPRequest, db: Session = Depends(get_db)):
    player = get_player(db, payload.telegram_id, lock=True)
    plano = payload.plano.strip().lower()
    if plano not in PLANOS:
        raise HTTPException(400, "Plano inválido.")
    cfg = PLANOS[plano]

    status = vip_ativo(player)
    if status.ativo and status.plano != plano:
        raise HTTPException(400, f"Você já tem o VIP {status.plano.capitalize()} ativo. Aguarde expirar para trocar de plano.")

    if (player.ouro_ninja or 0) < cfg["custo"]:
        raise HTTPException(402, "Ouro Ninja insuficiente.")

    agora = datetime.now(timezone.utc)
    base = status.expira_em if status.ativo else agora
    player.ouro_ninja = (player.ouro_ninja or 0) - cfg["custo"]
    player.vip_plano = plano
    player.vip_expira_em = base + timedelta(days=cfg["dias"])  # UTC com fuso (coluna timezone=True)
    db.commit()
    return {"mensagem": f"VIP {plano.capitalize()} ativado.", "ouro_ninja": player.ouro_ninja}
