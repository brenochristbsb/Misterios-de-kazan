"""
combate.py — Motor de combate do Shadow Clan RPG (Bloco 7 do win.txt).

Sequência estrita:
    1. Ataque Bruto        = (Atributo_Principal * 1.5) + Atributo_Secundario + Dano_Arma
    2. Mult. Mitigação     = 100 / (100 + Vigor_Alvo)
    3. Mod. Elemental      = 1.25 vantagem | 0.75 desvantagem | 1.0 neutro
                             (Taijutsu puro sempre 1.0)
    4. Dano_Base_Turno     = (Ataque Bruto * Mitigação * Elemental) * aleatório(0.95 a 1.05)

Regras de classe do Bloco 3 aplicadas aqui:
    - Taijutsu: ignora o modificador elemental; Portões dobram STR e AGI.
    - Ninjutsu: vantagem elemental dá 1.50 (em vez de 1.25).
    - Ninja Médico: dano verdadeiro (ignora Vigor/Defesa do alvo).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

# Ciclo elemental: Fogo > Vento > Relâmpago > Terra > Água > Fogo
# (cada elemento tem vantagem sobre o seguinte)
VANTAGEM_SOBRE = {
    "fogo": "vento",
    "vento": "relampago",
    "relampago": "terra",
    "terra": "agua",
    "agua": "fogo",
}

ATRIBUTOS = ("str", "int", "agi", "dex", "vig")

# Atributo principal / secundário de dano por classe (mesmos valores do schema.sql)
CLASSES = {
    "taijutsu":     {"principal": "str", "secundario": "agi", "ignora_elemento": True,  "dano_verdadeiro": False, "bonus_elemental": 1.25},
    "ninjutsu":     {"principal": "int", "secundario": "dex", "ignora_elemento": False, "dano_verdadeiro": False, "bonus_elemental": 1.50},
    "genjutsu":     {"principal": "int", "secundario": "agi", "ignora_elemento": False, "dano_verdadeiro": False, "bonus_elemental": 1.25},
    "marionetista": {"principal": "int", "secundario": "dex", "ignora_elemento": False, "dano_verdadeiro": False, "bonus_elemental": 1.25},
    "ninja_medico": {"principal": "int", "secundario": "dex", "ignora_elemento": False, "dano_verdadeiro": True,  "bonus_elemental": 1.25},
}


@dataclass
class Combatente:
    classe: str
    str: int = 0
    int: int = 0
    agi: int = 0
    dex: int = 0
    vig: int = 0
    dano_arma: int = 0
    elemento: Optional[str] = None
    portoes_abertos: int = 0          # Taijutsu: dobra STR e AGI em batalha

    def atributo(self, nome: str) -> int:
        valor = getattr(self, nome)
        if self.classe == "taijutsu" and self.portoes_abertos > 0 and nome in ("str", "agi"):
            valor *= 2
        return valor


@dataclass
class ResultadoDano:
    ataque_bruto: float
    mitigacao: float
    elemental: float
    aleatorio: float
    dano: int
    detalhes: dict = field(default_factory=dict)


def modificador_elemental(atacante: Combatente, alvo: Combatente) -> float:
    """1.25 (vantagem), 0.75 (desvantagem), 1.0 (neutro). Taijutsu puro = sempre 1.0."""
    regra = CLASSES[atacante.classe]
    if regra["ignora_elemento"]:
        return 1.0
    a, d = atacante.elemento, alvo.elemento
    if not a or not d or a == d:
        return 1.0
    if VANTAGEM_SOBRE.get(a) == d:
        return regra["bonus_elemental"]      # 1.25 padrão, 1.50 para Ninjutsu
    if VANTAGEM_SOBRE.get(d) == a:
        return 0.75
    return 1.0


def calcular_dano(
    atacante: Combatente,
    alvo: Combatente,
    rng: Optional[random.Random] = None,
) -> ResultadoDano:
    rng = rng or random
    regra = CLASSES[atacante.classe]

    # 1. Ataque Bruto
    ataque_bruto = (
        atacante.atributo(regra["principal"]) * 1.5
        + atacante.atributo(regra["secundario"])
        + atacante.dano_arma
    )

    # 2. Multiplicador de Mitigação (Ninja Médico: Bisturi de Chakra ignora Vigor)
    vigor_alvo = 0 if regra["dano_verdadeiro"] else alvo.vig
    mitigacao = 100 / (100 + vigor_alvo)

    # 3. Modificador Elemental
    elemental = modificador_elemental(atacante, alvo)

    # 4. Dano Base do Turno
    aleatorio = rng.uniform(0.95, 1.05)
    dano = ataque_bruto * mitigacao * elemental * aleatorio

    return ResultadoDano(
        ataque_bruto=ataque_bruto,
        mitigacao=mitigacao,
        elemental=elemental,
        aleatorio=aleatorio,
        dano=max(1, round(dano)),
        detalhes={"vigor_efetivo": vigor_alvo},
    )


if __name__ == "__main__":
    r = random.Random(42)
    naruto = Combatente("ninjutsu", int=40, dex=20, dano_arma=15, elemento="fogo")
    inimigo = Combatente("taijutsu", vig=30, elemento="vento")
    print(calcular_dano(naruto, inimigo, r))
