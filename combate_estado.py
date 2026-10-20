"""
combate_estado.py — Bloco 3: Máquina de estados para as 5 classes em combate.
"""
import random
from dataclasses import dataclass, field
from typing import Optional

FURIA_POR_GOLPE = 10
FURIA_POR_DANO_RECEBIDO = 5
FURIA_POR_PORTAO = 20
MAX_PORTOES = 8
CUSTO_HP_PORTAO_PCT = 0.05
HP_MARIONETE_BASE = 100
CURA_LIVRE_PCT = 0.5
CURA_LIVRE_CHAKRA = 30
DRENO_CHAKRA_POR_TURNO = 10
CHANCE_ATORDOAR = 0.30
CHANCE_CONFUSAO = 0.20
CHANCE_DRENO = 0.50


@dataclass
class EstadoLuta:
    classe_atacante: str
    classe_defensor: Optional[str] = None
    furia: int = 0
    portoes_ativos: int = 0
    marionete_hp: int = 0
    marionete_hp_max: int = 0
    cura_livre_aplicada: bool = False
    cura_valor: int = 0
    atordoado: bool = False
    confuso: bool = False
    dreno_chakra: bool = False
    efeito_aplicado: Optional[str] = None
    rng: random.Random = field(default_factory=random.Random)


def iniciar_marionete(estado: EstadoLuta, hp: int = HP_MARIONETE_BASE) -> None:
    """Liga a marionete (sem isso marionete_hp fica 0 e nada é absorvido)."""
    estado.marionete_hp_max = hp
    estado.marionete_hp = hp


def acumular_furia(estado: EstadoLuta, dano_causado: int, dano_recebido: int) -> None:
    if estado.classe_atacante != "taijutsu":
        return
    if dano_causado > 0:
        estado.furia += FURIA_POR_GOLPE
    if dano_recebido > 0:
        estado.furia += FURIA_POR_DANO_RECEBIDO
    estado.furia = min(estado.furia, MAX_PORTOES * FURIA_POR_PORTAO)


def absorver_dano_marionete(estado: EstadoLuta, dano: int) -> int:
    """A marionete absorve até o HP dela; o excedente passa para o marionetista."""
    if estado.classe_defensor != "marionetista" or estado.marionete_hp <= 0:
        return dano
    absorvido = min(dano, estado.marionete_hp)
    estado.marionete_hp -= absorvido
    return dano - absorvido


def rolar_efeito_genjutsu(estado: EstadoLuta) -> Optional[str]:
    if estado.classe_atacante != "genjutsu":
        return None
    r = estado.rng.random()
    if r < CHANCE_ATORDOAR:
        estado.atordoado = True
        estado.efeito_aplicado = "atordoamento"
        return "atordoamento"
    if r < CHANCE_ATORDOAR + CHANCE_CONFUSAO:
        estado.confuso = True
        estado.efeito_aplicado = "confusao"
        return "confusao"
    estado.dreno_chakra = True
    estado.efeito_aplicado = "dreno"
    return "dreno"


def verificar_atordoamento(estado: EstadoLuta) -> bool:
    if estado.atordoado:
        estado.atordoado = False
        return True
    return False


def verificar_confusao(estado: EstadoLuta) -> bool:
    if estado.confuso:
        estado.confuso = False
        return True
    return False


def iniciar_turno(estado: EstadoLuta) -> None:
    """Zera o que é válido só para o turno. Chamado no INÍCIO do golpe,
    para que o chamador ainda consiga ler efeito_aplicado depois dele."""
    estado.cura_livre_aplicada = False
    estado.cura_valor = 0
    estado.efeito_aplicado = None


# Mantido por compatibilidade com quem já importa limpar_turno.
limpar_turno = iniciar_turno
