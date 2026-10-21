"""combate_reativo.py - Shadow Clan RPG, GDD v1.1 (secoes 2, 9, 19.x, 20).

Servidor autoritativo: todo calculo acontece aqui, o cliente so exibe o log.
Uso:  python combate_reativo.py demo [Classe1 Classe2 Ambiente]
      python combate_reativo.py sim [N]      (matriz de vitorias entre classes)
Itens marcados (?) sao suposicoes de design para calibrar.
"""
from __future__ import annotations
import random
import sys
from collections import deque
from dataclasses import dataclass, field

ELEMENTOS = ["Fogo", "Vento", "Relâmpago", "Terra", "Água"]  # cada um vence o proximo
CLASSES = ["Taijutsu", "Ninjutsu", "Genjutsu", "Marionetista", "Kunoichi"]
POSTURAS = ["Pesada", "Rápida", "Perfurante", "Técnica"]
REACOES = ["Esquiva", "Bloqueio", "Contra-Golpe", "Dissipar"]
CONTRA = dict(zip(POSTURAS, REACOES))  # reacao que vence cada postura (GDD 20.1)
POSTURA = {
    "Pesada":     dict(dano=1.30, acerto=-10, init=-10, chakra=6),
    "Rápida":     dict(dano=0.80, acerto=5,   init=25,  chakra=4),
    "Perfurante": dict(dano=1.00, acerto=0,   init=0,   chakra=8),
    "Técnica":    dict(dano=1.00, acerto=0,   init=0,   chakra=10),
}
SURPRESA = 0.12          # postura executada vira aleatoria (GDD 20.1)
REACAO_CERTA = 0.40      # dano que passa quando a reacao acerta (-60%)
CONTRA_ATAQUE = 0.50     # contra-ataque = 50% de um golpe Rapido (?)
GATE_BONUS = [0, .25, .50, .75, .85, .95, 1.05, 1.15, 1.25]  # Portoes 1-8 (?)
MARIONETE_PCT = 0.12     # HP da marionete = 12% do HP maximo (?)
# Reacao propria de cada classe (GDD 20.1): substitui uma reacao base e ganha um efeito ao acertar
CLASSE_REACAO = {
    "Taijutsu":     ("Contra de Fúria", "Contra-Golpe"),
    "Ninjutsu":     ("Substituição Elemental", "Esquiva"),
    "Genjutsu":     ("Espelho", "Dissipar"),
    "Marionetista": ("Escudo de Marionete", "Bloqueio"),
    "Kunoichi":     ("Esquiva Cirúrgica", "Esquiva"),
}
# Coeficientes de balanceamento por classe (patches quinzenais mexem AQUI, nunca em preco real)
# Calibrados por simulacao (21/10/2026): ver secao 31.3 do GDD. Genjutsu precisa de coeficiente alto
# porque o controle sozinho nao compensa o dano de 70% nesta versao do motor.
CLASS_COEF = {"Taijutsu": 0.80, "Ninjutsu": 1.20, "Genjutsu": 1.70, "Marionetista": 0.73, "Kunoichi": 0.88}
# Pentagono de contra-ataque suave (GDD 2.2): +-5% de eficacia, nunca um counter absoluto
FORTE_CONTRA = {"Taijutsu": ("Marionetista", "Kunoichi"), "Ninjutsu": ("Taijutsu", "Genjutsu"),
                "Genjutsu": ("Taijutsu", "Marionetista"), "Marionetista": ("Ninjutsu", "Kunoichi"),
                "Kunoichi": ("Ninjutsu", "Genjutsu")}
TRIANGULO = 0.05
STATUS_ELEM = {"Fogo": ("Queimado", 3), "Relâmpago": ("Paralisado", 3),
               "Água": ("Congelado", 1), "Terra": ("Atordoado", 1)}

AMBIENTES = {
    "Floresta": dict(esquiva=10, reage={"Fogo": "Incêndio"}),
    "Deserto":  dict(regen=-2, elem={"Terra": .10}, reage={"Vento": "Tempestade de Areia"}),
    "Rio":      dict(elem={"Água": .15}, agi=-.10, reage={"Relâmpago": "Choque na Água"}),
    "Neve":     dict(congela=20, reage={"Fogo": "Lama"}),
    "Vulcão":   dict(dot=.02, reage={"Água": "Vapor"}),
    "Chuva":    dict(elem={"Água": .10, "Relâmpago": .10, "Fogo": -.15}, dura=3),
    "Névoa":    dict(acerto=-10, genj=.15, reage={"Vento": "Dissipa"}),
}
EFEITO_TURNOS = {"Incêndio": 3, "Tempestade de Areia": 3, "Lama": 3, "Vapor": 3}


def mod_elemental(classe, att, dfn):
    if classe == "Taijutsu" or not att or not dfn:
        return 1.0
    if ELEMENTOS[(ELEMENTOS.index(att) + 1) % 5] == dfn:
        return 1.5 if classe == "Ninjutsu" else 1.25
    if ELEMENTOS[(ELEMENTOS.index(dfn) + 1) % 5] == att:
        return 0.75
    return 1.0


@dataclass
class Lutador:
    nome: str
    classe: str
    elemento: str | None
    attrs: dict            # STR AGI INT DEX VIG
    arma: int = 20
    hp: float = 0
    hp_max: float = 0
    chakra: float = 0
    chakra_max: float = 0
    furia: int = 0
    portoes: int = 0
    marionete: float = 0
    marionete_max: float = 0
    veneno: int = 0
    status: dict = field(default_factory=dict)
    cd_cura: int = 0
    cd_combo: int = 0
    buff: float = 1.0
    ctrl_consec: int = 0
    sem_ctrl: int = 0
    historico: list = field(default_factory=list)
    jutsus: dict = field(default_factory=dict)   # chave -> dados do jutsu equipado
    cds: dict = field(default_factory=dict)
    selos: set = field(default_factory=set)
    supremo: str = ""
    supremo_usado: bool = False
    p_dano: float = 1.0
    p_crit: float = 0.0
    p_esq: float = 0.0
    ress: float = 1.0

    def __post_init__(self):
        if self.classe == "Taijutsu":
            self.elemento = None
        self.hp_max = (100 + self.attrs["VIG"] * 10) * (1.1 if self.classe == "Taijutsu" else 1.0)
        self.hp = self.hp_max
        if self.classe != "Taijutsu":
            self.chakra_max = 50 + self.attrs["INT"] * 3
            self.chakra = self.chakra_max
        if self.classe == "Marionetista":
            self.marionete_max = self.hp_max * MARIONETE_PCT
            self.marionete = self.marionete_max

    def attr(self, k):
        v = self.attrs[k]
        if self.classe == "Taijutsu" and k in ("STR", "AGI"):
            v *= 1 + GATE_BONUS[self.portoes]
        return v

    def principal(self):
        return self.attr("STR") if self.classe == "Taijutsu" else self.attr("INT")

    def secundario(self):
        return self.attr("AGI") if self.classe == "Taijutsu" else self.attr("DEX")


PRESETS = {  # mesmo total de pontos (150) para todas as classes
    "Taijutsu":     dict(STR=55, AGI=35, DEX=20, INT=5,  VIG=35),
    "Ninjutsu":     dict(STR=5,  AGI=25, DEX=30, INT=55, VIG=35),
    "Genjutsu":     dict(STR=5,  AGI=30, DEX=30, INT=55, VIG=30),
    "Marionetista": dict(STR=5,  AGI=25, DEX=30, INT=50, VIG=40),
    "Kunoichi":     dict(STR=5,  AGI=30, DEX=30, INT=55, VIG=30),
}


def criar(classe, nome=None, elemento="Fogo"):
    return Lutador(nome or classe, classe, elemento, dict(PRESETS[classe]))


# --------------------------------------------------------------- NARRADOR
FRASES = {
    "jutsu": ["{atk} molda o chakra e libera {jutsu}!", "{jutsu}! {atk} coloca tudo nesse golpe.", "O ar vibra: {atk} executa {jutsu}.", "{atk} sela as mãos e invoca {jutsu}!", "Sem aviso, {atk} lança {jutsu} contra {dfn}."],
    "abertura": ["Folhas giram no ar. {a} e {b} param frente a frente em {terreno}.", "{terreno}. {a} encara {b}, e ninguém recua.",
                 "O ar muda em {terreno}. {a} e {b} se medem em silêncio.",
                 "{terreno} aguarda. {a} contra {b}: o primeiro erro pode ser o último."],
    "golpe_0": ["{atk} só roça {dfn}: -{dano}.", "O golpe de {atk} perde força no caminho (-{dano}).", "{atk} acerta {dfn} de raspão (-{dano}).", "Um toque rápido de {atk}: -{dano}.",
                "{dfn} sente o golpe de {atk}, mas segue de pé (-{dano})."],
    "golpe_1": ["{atk} fecha a distância e acerta {dfn}: -{dano}.", "{dfn} cerra os dentes. -{dano}.", "{atk} ataca em postura {postura}! {dfn} recua: -{dano}.",
                "{atk} avança e conecta. -{dano} em {dfn}.",
                "O impacto ecoa. {dfn} perde {dano} de vida."],
    "golpe_2": ["{atk} ruge e desfere o golpe da luta! -{dano}!", "O chão racha sob {dfn}. -{dano}!", "{atk} entra com tudo! O golpe abre {dfn}: -{dano}!",
                "A pancada de {atk} faz o chão tremer. -{dano}!",
                "{dfn} é lançado para trás. -{dano}!"],
    "critico": ["{atk} vê a abertura e não perdoa!", "Ponto fraco! {atk} acerta em cheio {dfn}.", "CRÍTICO! {atk} acha a brecha de {dfn}.", "Um golpe perfeito de {atk}. Isso vai deixar marca!"],
    "erro": ["{dfn} lê o movimento e desvia por pouco.", "{atk} avança, mas só encontra sombra.", "{atk} ataca, mas {dfn} já não está lá.", "O golpe de {atk} corta só o ar."],
    "esquiva": ["{dfn} some e reaparece fora do alcance!", "Rápido demais: {dfn} esquiva sem esforço.", "{dfn} escorrega para fora da trajetória no último instante!"],
    "reacao": ["{dfn} já esperava isso: {reacao}!", "{reacao}! O plano de {atk} desmorona.", "{dfn} leu {atk} e responde com {reacao}! O dano cai pela metade e mais.",
               "{reacao}! {dfn} antecipou tudo."],
    "contra": ["{dfn} aproveita a brecha e revida: -{dano} em {atk}!", "O jogo vira por um instante: {atk} leva -{dano}.", "E {dfn} devolve na hora: -{dano} em {atk}!", "A resposta vem rápida. {atk} sofre -{dano}."],
    "quebra": ["Estilhaços de madeira voam: a marionete de {dfn} não resiste.", "A marionete de {dfn} racha e cai em pedaços!", "Os fios se partem. A marionete de {dfn} se foi."],
    "portao": ["{atk} abre o Portão {n}. A pele arde, o poder explode.",
               "Portão {n} aberto! O corpo de {atk} paga caro por isso."],
    "status": ["{dfn} cambaleia: {status}.", "{dfn} fica {status}!", "{dfn} sente o efeito: {status}."],
    "cura": ["O chakra de {atk} costura a carne: +{dano}.", "{atk} respira fundo e recupera +{dano}.", "{atk} fecha as próprias feridas com chakra: +{dano}."],
    "combo": ["{atk} encadeia a técnica em COMBO!"],
    "surpresa": ["{atk} muda de ideia no meio do movimento. Ninguém previu!"],
    "ambiente": ["{evento}! O campo também luta."],
    "virada": ["VIRADA! {atk} assume a dianteira da luta."],
    "baixo": ["{dfn} está por um fio.", "Mais um golpe e {dfn} cai.", "{dfn} respira com dificuldade. Resta quase nada."],
    "final": ["{dfn} tomba. {atk} permanece de pé.", "Silêncio. {atk} venceu, {dfn} está no chão.", "O último golpe de {atk} encerra a luta. {dfn} cai."],
    "confuso": ["{atk} está confuso e golpeia a si mesmo: -{dano}!", "A ilusão engana {atk}, que se fere sozinho (-{dano})."],
    "dreno": ["{atk} drena o chakra de {dfn} (-{dano})."],
}


class Narrador:
    def __init__(self, ambiente, a, b, N=4):
        self.ambiente, self.a, self.b = ambiente, a, b
        self.linhas, self.recentes = [], deque(maxlen=N)
        self.maior = (0, "", "")
        self.viradas = 0

    def dizer(self, ev, **v):
        bancos = FRASES[ev]
        idx = [i for i in range(len(bancos)) if (ev, i) not in self.recentes] or list(range(len(bancos)))
        i = random.choice(idx)
        self.recentes.append((ev, i))
        self.linhas.append(bancos[i].format(**v))

    def fase(self, titulo):
        self.linhas.append(f"— {titulo} —")

    def cronica(self, vencedor, rodadas):
        v = vencedor.nome if vencedor else "ninguém"
        d, quem, alvo = self.maior
        return [f"{self.a.nome} e {self.b.nome} se enfrentaram em {self.ambiente}.",
                f"O maior golpe foi de {quem} em {alvo}: {d}." if d else "Foi uma luta de nervos.",
                f"A liderança mudou {self.viradas} vez(es)." if self.viradas else "Ninguém conseguiu virar a luta.",
                f"Em {rodadas} rodadas, {v} resistiu mais.",
                "A aldeia vai falar disso por semanas."]


# ----------------------------------------------------------------- MOTOR
class Batalha:
    def __init__(self, a, b, ambiente="Floresta", boss=False, narrar=True, decisores=None):
        self.ls = [a, b]
        self.ambiente = ambiente
        self.cfg = AMBIENTES[ambiente]
        self.boss = boss
        self.rodada = 0
        self.efeitos = {}
        self.nevoa_off = False
        self.decisores = decisores or [bot, bot]
        self.n = Narrador(ambiente, a, b)
        self.narrar = narrar
        self.lider = None
        self.max_rodadas = 30
        self._aberta = False
        self._encerrada = False
        self.vencedor = None
        self.escolha = None

    # --- ambiente
    def amb(self, k, default=0):
        if self.ambiente == "Chuva" and self.rodada > self.cfg["dura"]:
            return default
        if self.ambiente == "Névoa" and self.nevoa_off and k in ("acerto", "genj"):
            return default
        return self.cfg.get(k, default)

    def agi_eff(self, l):
        m = 1 + self.amb("agi")
        if "Lama" in self.efeitos:
            m -= .15
        if "Paralisado" in l.status:
            m -= .30
        return l.attr("AGI") * m

    def acerto(self, att, dfn, p):
        v = 75 + (att.attr("DEX") - self.agi_eff(dfn)) * .5 + POSTURA[p]["acerto"] + self.amb("acerto")
        if "Tempestade de Areia" in self.efeitos:
            v -= 10
        if "Vapor" in self.efeitos:
            v -= 10
        return max(50, min(95, v))

    # --- dano
    def calc(self, att, dfn, p, interno=False, mult=1.0, pode_crit=True):
        bruto = att.principal() * 1.5 + att.secundario() + att.arma
        vig = dfn.attr("VIG") * (0.8 if interno else 1.0)
        mit = 100 / (100 + vig)
        if p == "Perfurante":
            mit += (1 - mit) * .2
        f = POSTURA[p]["dano"] * mult * random.uniform(.95, 1.05) * CLASS_COEF[att.classe] * att.buff * att.p_dano * att.ress
        if att.supremo == "vinganca" and att.hp < att.hp_max * .3:
            f *= 1.25
        if "olho_vazio" in att.selos and any(k in dfn.status for k in ("Congelado", "Atordoado", "Confusão", "Paralisado")):
            f *= 1.10
        f *= mod_elemental(att.classe, att.elemento, dfn.elemento)
        if att.elemento:
            f *= 1 + self.amb("elem", {}).get(att.elemento, 0)
        if att.classe == "Genjutsu":
            f *= 0.70 * (1 + self.amb("genj") + (.15 if "Vapor" in self.efeitos else 0))
        if "Congelado" in dfn.status or "Atordoado" in dfn.status:
            f *= 1.2  # alvo sob controle fica vulneravel
        if dfn.classe in FORTE_CONTRA[att.classe]:
            f *= 1 + TRIANGULO
        elif att.classe in FORTE_CONTRA[dfn.classe]:
            f *= 1 - TRIANGULO
        crit = pode_crit and random.random() < min(.40, .05 + att.attr("DEX") * .002 + att.p_crit)
        if crit:
            f *= 1.5
        if att.classe == "Kunoichi":  # 60% dano verdadeiro, coeficiente x0,85
            return bruto * f * 0.85 * (0.6 + 0.4 * mit), crit
        return bruto * f * mit, crit

    def receber(self, dfn, dano):
        """Marionete absorve 100% do dano direto; o excedente se perde."""
        if dfn.supremo == "veu_sombrio" and not dfn.supremo_usado:
            dano *= .5
            dfn.supremo_usado = True
        if dfn.classe == "Marionetista" and dfn.marionete > 0:
            if dano < dfn.marionete:
                dfn.marionete -= dano
                return 0, False
            dfn.marionete = 0
            return 0, True
        dfn.hp -= dano
        if dfn.hp <= 0 and dfn.supremo == "ultimo_folego" and not dfn.supremo_usado:
            dfn.hp, dfn.supremo_usado = 1, True
            self.n.linhas.append(f"{dfn.nome} resiste ao golpe fatal por pura vontade!")
        return dano, False

    # --- ataque
    def atacar(self, att, dfn, p, reacoes, jutsu=None):
        n, d = self.n, dict(atk=att.nome, dfn=dfn.nome, postura=p)
        mult, interno, combo = 1.0, False, False
        J = self.escolher_jutsu(att, p, jutsu)
        if J:
            mult *= J["dano"]
            n.dizer("jutsu", jutsu=J["nome"], **d)
        elif att.classe == "Taijutsu":
            if p == "Técnica":
                if att.furia >= 50:
                    att.furia -= 50
                    interno, mult = True, 1.2
                else:
                    mult = 0.9
        else:
            custo = POSTURA[p]["chakra"]
            if p == "Técnica" and att.historico[-1:] == ["Técnica"] and att.cd_combo == 0:
                combo, custo = True, custo + 8
            if att.chakra >= custo:
                att.chakra -= custo
            else:
                combo, mult = False, 0.7
        if combo:
            mult *= 1.25
            att.cd_combo = 3
            n.dizer("combo", **d)
        esq = min(30, dfn.attr("AGI") * .25 + self.amb("esquiva") + dfn.p_esq * 100)
        if random.random() * 100 > self.acerto(att, dfn, p):
            return n.dizer("erro", **d)
        usada = self.reacao_usada(dfn, p, reacoes)
        certa = usada is not None
        if not certa and random.random() * 100 < esq:
            if "danca_furia" in dfn.selos:
                dfn.buff = max(dfn.buff, 1.15)
            return n.dizer("esquiva", **d)
        dano, crit = self.calc(att, dfn, p, interno, mult)
        att.buff = 1.0
        if crit:
            n.dizer("critico", **d)
            if "sangue_frio" in att.selos:
                att.hp = min(att.hp_max, att.hp + att.hp_max * .04)
        if certa:
            dano *= REACAO_CERTA
            n.dizer("reacao", reacao=usada, **d)
        real, quebrou = self.receber(dfn, dano)
        pct = dano / dfn.hp_max
        n.dizer("golpe_%d" % (0 if pct < .08 else 1 if pct < .18 else 2), dano=int(dano), **d)
        if dano > n.maior[0]:
            n.maior = (int(dano), att.nome, dfn.nome)
        if quebrou:
            n.dizer("quebra", **d)
        if att.classe == "Taijutsu":
            att.furia = min(100, att.furia + 8)
        if dfn.classe == "Taijutsu":
            dfn.furia = min(100, dfn.furia + 5)
        if dfn.hp <= 0:
            return n.dizer("final", **d)
        if "sede_chakra" in att.selos:
            if att.chakra_max:
                att.chakra = min(att.chakra_max, att.chakra + 3)
            else:
                att.furia = min(100, att.furia + 4)
        if J:
            self.efeito_jutsu(att, dfn, J, d)
        elif p == "Técnica":
            self.efeito_tecnica(att, dfn, d)
        cmult = CONTRA_ATAQUE
        if usada == CLASSE_REACAO[dfn.classe][0]:
            cmult = self.efeito_reacao(dfn, att, dano_bruto=dano / REACAO_CERTA)
        if certa and dfn.hp > 0 and att.hp > 0:  # contra-ataque
            c, _ = self.calc(dfn, att, "Rápida", mult=cmult, pode_crit=False)
            real, quebrou = self.receber(att, c)
            self.n.dizer("contra", atk=att.nome, dfn=dfn.nome, dano=int(c))
            if quebrou:
                n.dizer("quebra", atk=dfn.nome, dfn=att.nome, postura=p)
            if att.hp <= 0:
                n.dizer("final", atk=dfn.nome, dfn=att.nome, postura=p)

    def reacao_usada(self, dfn, p, reacoes):
        base = CONTRA[p]
        nome_cls, base_cls = CLASSE_REACAO[dfn.classe]
        if nome_cls in reacoes and base_cls == base:
            return nome_cls
        return base if base in reacoes else None

    def efeito_reacao(self, dfn, att, dano_bruto):
        """Efeito extra da reacao propria da classe; devolve o multiplicador do contra-ataque."""
        c = dfn.classe
        if c == "Taijutsu":
            dfn.furia = min(100, dfn.furia + 10)
            return CONTRA_ATAQUE * 1.3
        if c == "Ninjutsu":
            dfn.buff = 1.15
        elif c == "Genjutsu":
            self.receber(att, dano_bruto * .30)
        elif c == "Marionetista":
            dfn.marionete = min(dfn.marionete_max, dfn.marionete + dfn.marionete_max * .10)
        elif c == "Kunoichi":
            dfn.hp = min(dfn.hp_max, dfn.hp + dfn.hp_max * .04)
            dfn.chakra = min(dfn.chakra_max, dfn.chakra + 8)
        return CONTRA_ATAQUE

    def escolher_jutsu(self, att, p, jutsu):
        """Jutsu so sai na postura Tecnica. jutsu=None: o bot escolhe; jutsu='': tecnica basica."""
        if p != "Técnica" or not att.jutsus or jutsu == "":
            return None
        cand = [jutsu] if jutsu else sorted(att.jutsus, key=lambda k: -att.jutsus[k]["dano"])
        for k in cand:
            J = att.jutsus.get(k)
            if not J or att.cds.get(k, 0) > 0:
                continue
            pool = "furia" if att.classe == "Taijutsu" else "chakra"
            if getattr(att, pool) < J["custo"]:
                continue
            setattr(att, pool, getattr(att, pool) - J["custo"])
            att.cds[k] = J["cd"]
            return J
        return None

    def efeito_jutsu(self, att, dfn, J, d):
        n, x = self.n, J.get("x", {})
        if "status" in x:
            nome, t, ch = x["status"]
            if nome == "Queimado" and "brasa_viva" in att.selos and nome in dfn.status:
                dfn.status[nome] = t + 1
            elif random.random() < ch and nome not in dfn.status and not (self.boss and nome in ("Atordoado", "Congelado") and random.random() < .5):
                dfn.status[nome] = t
                n.dizer("status", status=nome.lower(), **d)
        if x.get("veneno"):
            dfn.veneno = min(5, dfn.veneno + x["veneno"] + (1 if "veneno_duplo" in att.selos else 0))
            n.dizer("status", status=f"envenenado (x{dfn.veneno})", **d)
        if x.get("dreno") and dfn.chakra_max:
            dr = min(dfn.chakra, x["dreno"])
            dfn.chakra -= dr
            if att.chakra_max and x.get("sifao"):
                att.chakra = min(att.chakra_max, att.chakra + dr)
            n.dizer("dreno", dano=int(dr), **d)
        if x.get("cura"):
            h = att.hp_max * x["cura"]
            att.hp = min(att.hp_max, att.hp + h)
            n.dizer("cura", atk=att.nome, dano=int(h))
        if x.get("escudo") and att.classe == "Marionetista":
            att.marionete = min(att.marionete_max, att.marionete + att.marionete_max * x["escudo"])
        if x.get("buff"):
            att.buff = max(att.buff, x["buff"])
        if x.get("furia"):
            att.furia = min(100, att.furia + x["furia"])

    def efeito_tecnica(self, att, dfn, d):
        n = self.n
        if att.classe == "Marionetista":
            dfn.veneno = min(5, dfn.veneno + 1)
            n.dizer("status", status=f"envenenado (x{dfn.veneno})", **d)
        elif att.classe == "Genjutsu":
            ch = .80 * (.7 ** att.ctrl_consec) * (.5 if self.boss else 1)
            if dfn.classe == "Marionetista" and dfn.marionete > 0:
                ch *= .5
            if random.random() < ch:
                dfn.status["Atordoado"] = 1
                att.ctrl_consec += 1
                att.sem_ctrl = 0
                n.dizer("status", status="atordoado", **d)
            elif random.random() < ch * .8 and "Confusão" not in dfn.status:
                dfn.status["Confusão"] = 2
                att.ctrl_consec += 1
                att.sem_ctrl = 0
                n.dizer("status", status="confuso", **d)
            if dfn.chakra_max:
                dr = min(dfn.chakra, 15)
                dfn.chakra -= dr
                n.dizer("dreno", dano=int(dr), **d)
        elif att.elemento in STATUS_ELEM:
            nome, t = STATUS_ELEM[att.elemento]
            ch = .35 + (self.amb("congela") / 100 if nome == "Congelado" else 0)
            if random.random() < ch and nome not in dfn.status:
                dfn.status[nome] = t
                n.dizer("status", status=nome.lower(), **d)
        if att.elemento and att.classe != "Taijutsu":  # o campo reage
            ev = self.cfg.get("reage", {}).get(att.elemento)
            if ev:
                self.reagir_ambiente(ev)

    def reagir_ambiente(self, ev):
        self.n.dizer("ambiente", evento=ev)
        if ev == "Choque na Água":
            for l in self.ls:
                l.hp -= l.hp_max * .05
        elif ev == "Dissipa":
            self.nevoa_off = True
        else:
            self.efeitos[ev] = EFEITO_TURNOS[ev]

    # --- rodada
    def _rodada(self):
        ls, n = self.ls, self.n
        decs = [self.decisores[i](ls[i], ls[1 - i], self) for i in (0, 1)]
        for l, d in zip(ls, decs):
            if d.get("portao") and l.classe == "Taijutsu" and l.portoes < 8:
                l.portoes += 1
                n.dizer("portao", atk=l.nome, n=l.portoes)
            if d.get("curar") and l.classe == "Kunoichi" and l.cd_cura == 0 and l.chakra >= 25 and l.hp < l.hp_max:
                h = l.hp_max * .25
                l.hp = min(l.hp_max, l.hp + h)
                l.chakra -= 25
                l.cd_cura = 3
                n.dizer("cura", atk=l.nome, dano=int(h))
        posts = []
        for l, d in zip(ls, decs):
            p = d["postura"]
            if random.random() < SURPRESA:
                p = random.choice(POSTURAS)
                n.dizer("surpresa", atk=l.nome)
            posts.append(p)
        ordem = sorted((0, 1), key=lambda i: -(self.agi_eff(ls[i]) + POSTURA[posts[i]]["init"] + random.random()))
        for i in ordem:
            att, dfn = ls[i], ls[1 - i]
            if att.hp <= 0 or dfn.hp <= 0:
                break
            if "Atordoado" in att.status or "Congelado" in att.status:
                continue
            if "Paralisado" in att.status and random.random() < .25:
                continue
            if "Confusão" in att.status and random.random() < .35:
                c, _ = self.calc(att, att, posts[i], mult=.6, pode_crit=False)
                self.receber(att, c)
                n.dizer("confuso", atk=att.nome, dano=int(c))
                continue
            self.atacar(att, dfn, posts[i], decs[1 - i].get("reacoes", [])[:2], decs[i].get("jutsu"))
            att.historico.append(posts[i])
        self.fim_rodada()

    def fim_rodada(self):
        for l in self.ls:
            if l.hp <= 0:
                continue
            dot = l.hp_max * (self.amb("dot") + (.03 if "Incêndio" in self.efeitos else 0))
            if "Queimado" in l.status:
                dot += l.hp_max * .04
            dot += l.hp_max * .025 * l.veneno
            l.hp -= dot
            if l.portoes:
                custo = l.hp * .05 * l.portoes
                if l.portoes == 8:
                    custo += l.hp_max * .10  # Portao 8 pode matar (?)
                    l.hp -= custo
                else:
                    l.hp = max(1, l.hp - custo)
            if l.chakra_max:
                l.chakra = min(l.chakra_max, l.chakra + 3 + self.amb("regen"))
            l.status = {k: v - 1 for k, v in l.status.items() if v > 1}
            l.cd_cura = max(0, l.cd_cura - 1)
            l.cds = {k: v - 1 for k, v in l.cds.items() if v > 1}
            l.cd_combo = max(0, l.cd_combo - 1)
            l.sem_ctrl += 1
            if l.sem_ctrl >= 3:
                l.ctrl_consec = 0
        self.efeitos = {k: v - 1 for k, v in self.efeitos.items() if v > 1}
        a, b = self.ls
        lider = 0 if a.hp / a.hp_max >= b.hp / b.hp_max else 1
        if self.lider is not None and lider != self.lider and self.rodada > 2:
            self.n.viradas += 1
            self.n.dizer("virada", atk=self.ls[lider].nome)
        self.lider = lider
        for l, o in ((a, b), (b, a)):
            if 0 < l.hp < l.hp_max * .15 and not getattr(l, "_avisado", False):
                l._avisado = True
                self.n.fase("Clímax")
                self.n.dizer("baixo", dfn=l.nome)

    # --- controle da luta (a API avanca uma rodada por chamada; o servidor decide tudo)
    def abrir(self):
        if self._aberta:
            return
        self._aberta = True
        self.n.fase("Abertura")
        self.n.dizer("abertura", a=self.ls[0].nome, b=self.ls[1].nome, terreno=self.ambiente)
        self.n.fase("Escalada")

    def terminou(self):
        return self.rodada >= self.max_rodadas or any(l.hp <= 0 for l in self.ls)

    def passo(self):
        self.abrir()
        if self.terminou():
            return self.encerrar()
        self.rodada += 1
        self._rodada()
        if self.terminou():
            self.encerrar()

    def encerrar(self):
        if self._encerrada:
            return self.vencedor
        self._encerrada = True
        a, b = self.ls
        if a.hp > 0 and b.hp > 0:
            v = a if a.hp / a.hp_max >= b.hp / b.hp_max else b
        else:
            v = a if a.hp > 0 else b if b.hp > 0 else None
        self.n.fase("Desfecho")
        self.vencedor = v
        return v

    def lutar(self, max_rodadas=30):
        self.max_rodadas = max_rodadas
        while not self.terminou():
            self.passo()
        return self.encerrar()


def escolha_jogador(eu, rival, bat):
    """Decisor usado pela API: le a decisao que o jogador enviou para esta rodada."""
    return bat.escolha


# -------------------------------------------------------- DECISORES (bots)
def bot(eu, rival, bat):
    """Le os 3 ultimos turnos do rival e cobre as 2 posturas mais provaveis."""
    hist = rival.historico[-3:]
    cont = {p: hist.count(p) + random.random() * .8 for p in POSTURAS}
    prov = sorted(POSTURAS, key=lambda p: -cont[p])[:2]
    nome_cls, base_cls = CLASSE_REACAO[eu.classe]
    reacoes = [nome_cls if CONTRA[p] == base_cls else CONTRA[p] for p in prov]
    pesos = {"Taijutsu": [3, 3, 2, 2], "Ninjutsu": [2, 2, 2, 4], "Genjutsu": [1, 2, 2, 5],
             "Marionetista": [2, 2, 3, 3], "Kunoichi": [2, 3, 3, 2]}[eu.classe]
    p = random.choices(POSTURAS, pesos)[0]
    if eu.classe == "Taijutsu" and eu.furia >= 50:
        p = "Técnica"
    d = dict(postura=p, reacoes=reacoes)
    if eu.classe == "Taijutsu" and eu.portoes < 3 and eu.hp > eu.hp_max * .6:
        d["portao"] = True
    if eu.classe == "Kunoichi" and eu.hp < eu.hp_max * .55:
        d["curar"] = True
    return d


def decisor_monstro(eu, rival, bat):
    """Monstros usam o bot; chefes seguem o golpe que anunciaram no turno anterior (GDD 20.1)."""
    d = bot(eu, rival, bat)
    if getattr(bat, "plano_boss", None):
        d["postura"] = bat.plano_boss
    return d


def bot_aleatorio(eu, rival, bat):
    return dict(postura=random.choice(POSTURAS), reacoes=random.sample(REACOES, 2))


# ----------------------------------------------------------------- CLI
def demo(c1="Marionetista", c2="Taijutsu", amb="Floresta"):
    a, b = criar(c1, c1 + " (A)", "Fogo"), criar(c2, c2 + " (B)", "Vento")
    bat = Batalha(a, b, amb)
    v = bat.lutar()
    print("\n".join(bat.n.linhas))
    print("\nVencedor:", v.nome if v else "empate", f"| rodadas: {bat.rodada}")
    print("\nCRÔNICA:\n" + "\n".join(bat.n.cronica(v, bat.rodada)))


def sim(N=300):
    print(f"Taxa de vitória da linha contra a coluna (N={N} por par, ambientes aleatórios)\n")
    print(" " * 14 + "".join(f"{c[:6]:>8}" for c in CLASSES) + "   média")
    for c1 in CLASSES:
        row, tot = [], []
        for c2 in CLASSES:
            if c1 == c2:
                row.append("   --   ")
                continue
            w = 0
            for _ in range(N):
                a = criar(c1, "A", random.choice(ELEMENTOS))
                b = criar(c2, "B", random.choice(ELEMENTOS))
                v = Batalha(a, b, random.choice(list(AMBIENTES)), narrar=False).lutar()
                w += v is a
            tot.append(w / N)
            row.append(f"{w / N:8.0%}")
        print(f"{c1:<14}" + "".join(row) + f"  {sum(tot) / len(tot):6.1%}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "sim":
        sim(int(sys.argv[2]) if len(sys.argv) > 2 else 300)
    else:
        demo(*sys.argv[2:5])
