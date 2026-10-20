"""
social_reserva.py — Chat (global, guilda e privado) e troca de nome. Plano B do social_extra.py.

O api.py carrega o social_extra primeiro. Este módulo vem logo depois e só atende o que o social_extra não tiver: o FastAPI usa
a primeira rota registrada para cada caminho. Se o social_extra já cuida do chat e do nome, este arquivo não muda nada.

Rotas (formato exatamente igual ao que o index.html espera):
    POST /api/chat/conversas  {telegram_id}                                        -> {conversas: [nomes]}
    POST /api/chat/ler        {telegram_id, canal, com, depois}                    -> {mensagens: [...]}
    POST /api/chat/enviar     {telegram_id, canal, com, texto, depois}             -> {mensagens: [...]}  (as novas, inclusive a sua)
    POST /api/conta/nome      {telegram_id, novo_nome}                             -> {mensagem}
Canais: global (todos), guilda (só da sua guilda) e dm (privado, por nome do ninja). Mensagens somem após 7 dias.
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Integer, String, and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from api import get_db, get_player
from models import Base, Player

router = APIRouter()

TEXTO_MAX = 200
INTERVALO_S = 2  # tempo mínimo entre duas mensagens do mesmo jogador
RETENCAO_DIAS = 7
CUSTO_NOME = 2000
BRT = timezone(timedelta(hours=-3))  # horário de Brasília, só para exibir


class MensagemChat(Base):
    __tablename__ = "chat_mensagens"

    id: Mapped[int] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(8), index=True)
    autor_id: Mapped[int] = mapped_column(Integer, index=True)
    autor: Mapped[str] = mapped_column(String(32))
    dest_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)    # só no privado
    guilda_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)  # só no canal da guilda
    texto: Mapped[str] = mapped_column(String(TEXTO_MAX))
    criado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def limpar(texto: str) -> str:
    """Tira caracteres de controle e espaços repetidos."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", texto).split())[:TEXTO_MAX]


def _msg(m: MensagemChat, eu_id: int) -> dict:
    return {"id": m.id, "autor": m.autor, "hora": _utc(m.criado).astimezone(BRT).strftime("%H:%M"), "texto": m.texto,
            "eu": m.autor_id == eu_id}


class LerReq(BaseModel):
    telegram_id: int = Field(gt=0)
    canal: Literal["global", "guilda", "dm"]
    com: Optional[str] = Field(default=None, max_length=32)
    depois: int = Field(default=0, ge=0)


class EnviarReq(LerReq):
    texto: str = Field(min_length=1, max_length=TEXTO_MAX)


class ConversasReq(BaseModel):
    telegram_id: int = Field(gt=0)


def _filtro(db: Session, p: Player, canal: str, com: Optional[str]):
    """Condição SQL do canal e o destinatário (no privado)."""
    if canal == "global":
        return MensagemChat.canal == "global", None
    if canal == "guilda":
        if not p.guilda_id:
            raise HTTPException(409, "Você não está em uma guilda.")
        return and_(MensagemChat.canal == "guilda", MensagemChat.guilda_id == p.guilda_id), None
    if not com or not com.strip():
        raise HTTPException(400, "Informe o nome do ninja.")
    t = db.scalar(select(Player).where(func.lower(Player.ninja_name) == com.strip().lower()))
    if t is None:
        raise HTTPException(404, "Esse ninja não existe.")
    if t.id == p.id:
        raise HTTPException(400, "Você não pode conversar consigo mesmo.")
    return and_(MensagemChat.canal == "dm", or_(and_(MensagemChat.autor_id == p.id, MensagemChat.dest_id == t.id),
                                                and_(MensagemChat.autor_id == t.id, MensagemChat.dest_id == p.id))), t


def _ler(db: Session, filtro, depois: int, eu_id: int) -> list:
    if depois:
        rows = db.scalars(select(MensagemChat).where(filtro, MensagemChat.id > depois).order_by(MensagemChat.id).limit(100)).all()
    else:  # primeira leitura: as 50 mais recentes, em ordem
        rows = list(reversed(db.scalars(select(MensagemChat).where(filtro).order_by(MensagemChat.id.desc()).limit(50)).all()))
    return [_msg(m, eu_id) for m in rows]


@router.post("/api/chat/ler")
def chat_ler(payload: LerReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id)
    filtro, _ = _filtro(db, p, payload.canal, payload.com)
    return {"mensagens": _ler(db, filtro, payload.depois, p.id)}


@router.post("/api/chat/enviar")
def chat_enviar(payload: EnviarReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id)
    filtro, t = _filtro(db, p, payload.canal, payload.com)
    texto = limpar(payload.texto)
    if not texto:
        raise HTTPException(400, "Mensagem vazia.")
    agora = datetime.now(timezone.utc)
    ult = db.scalar(select(func.max(MensagemChat.criado)).where(MensagemChat.autor_id == p.id))
    if ult is not None and (agora - _utc(ult)).total_seconds() < INTERVALO_S:
        raise HTTPException(429, "Devagar: espere um instante para enviar outra mensagem.")
    db.add(MensagemChat(canal=payload.canal, autor_id=p.id, autor=p.ninja_name, dest_id=t.id if t else None,
                        guilda_id=p.guilda_id if payload.canal == "guilda" else None, texto=texto))
    if random.random() < 0.02:  # limpeza ocasional das mensagens antigas
        db.execute(delete(MensagemChat).where(MensagemChat.criado < agora - timedelta(days=RETENCAO_DIAS)))
    db.commit()
    return {"mensagens": _ler(db, filtro, payload.depois, p.id)}


@router.post("/api/chat/conversas")
def chat_conversas(payload: ConversasReq, db: Session = Depends(get_db)):
    """Com quem você conversou por mensagem privada, da mais recente para a mais antiga."""
    p = get_player(db, payload.telegram_id)
    rows = db.execute(select(MensagemChat.autor_id, MensagemChat.dest_id)
                      .where(MensagemChat.canal == "dm", or_(MensagemChat.autor_id == p.id, MensagemChat.dest_id == p.id))
                      .order_by(MensagemChat.id.desc()).limit(200)).all()
    ids: list = []
    for a, d in rows:
        outro = d if a == p.id else a
        if outro not in ids:
            ids.append(outro)
        if len(ids) >= 15:
            break
    nomes = {x.id: x.ninja_name for x in db.scalars(select(Player).where(Player.id.in_(ids or [-1])))}
    return {"conversas": [nomes[i] for i in ids if i in nomes]}


class NomeReq(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    telegram_id: int = Field(gt=0)
    novo_nome: str = Field(min_length=3, max_length=32, pattern=r"^[\w ]+$")


@router.post("/api/conta/nome")
def trocar_nome(payload: NomeReq, db: Session = Depends(get_db)):
    """Troca o nome do ninja por 2.000 ryos. Os nomes são únicos (sem diferenciar maiúsculas)."""
    p = get_player(db, payload.telegram_id, lock=True)
    novo = " ".join(payload.novo_nome.split())
    if len(novo) < 3:
        raise HTTPException(400, "O nome precisa de pelo menos 3 caracteres.")
    if novo == p.ninja_name:
        raise HTTPException(400, "Esse já é o seu nome.")
    if db.scalar(select(Player.id).where(func.lower(Player.ninja_name) == novo.lower(), Player.id != p.id)):
        raise HTTPException(409, "Esse nome já está em uso.")
    if p.ryos < CUSTO_NOME:
        raise HTTPException(400, f"Trocar o nome custa {CUSTO_NOME} ryos.")
    p.ryos -= CUSTO_NOME
    p.ninja_name = novo
    db.execute(update(MensagemChat).where(MensagemChat.autor_id == p.id).values(autor=novo))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Esse nome já está em uso.")
    return {"mensagem": f"Agora você é {novo}."}
