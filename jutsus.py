"""jutsus.py - Arvore de Jutsus, Selos, Ressonancia e Codigo de Build (GDD 19).

Regras: 4 jutsus ativos (5o no nivel 30, 6o no 60); 1 ponto por nivel; a arvore custa mais
do que o jogador ganha, entao sempre se abre mao de algo; nenhum no passa de +15% em um efeito;
3 jutsus com a mesma tag = Ressonancia (+8%); Selos mudam o COMPORTAMENTO do golpe.
"""
from __future__ import annotations
import base64
import json

import combate_reativo as C
from governo import RegraErro

PASSIVO_MAX = 0.15
RESSONANCIA = 1.08
RESPEC_BASE = 1000                      # 1o respec gratis; depois 1000 x (respecs ja feitos)
PRECO_SELO, PRECO_SUPREMO = 600, 5000
SOCKETS = {"comum": 1, "incomum": 1, "raro": 2, "epico": 2, "lendario": 3}
PATENTE_SUPREMO = "Chunin"
ORDEM_PATENTE = ["Estudante", "Genin", "Chunin", "Jounin", "Jounin Elite"]
NIVEL_MIN = [1, 15, 10, 40, 30]         # por posicao na cadeia: A1, A2, P1, A3, P2
CUSTO = [5, 7, 6, 10, 9]

SELOS = {
    "brasa_viva":   ("Selo da Brasa Viva", "Queimadura de jutsu se reaplica e dura 1 turno a mais."),
    "danca_furia":  ("Selo da Dança", "Esquivar um golpe deixa seu próximo ataque 15% mais forte."),
    "veneno_duplo": ("Selo da Víbora", "Seus jutsus aplicam +1 acúmulo de veneno."),
    "sede_chakra":  ("Selo da Sede", "Cada golpe que acerta devolve 3 de chakra (ou 4 de fúria)."),
    "sangue_frio":  ("Selo do Sangue Frio", "Críticos curam 4% da sua vida máxima."),
    "olho_vazio":   ("Selo do Olho Vazio", "+10% de dano em alvos sob controle (atordoado, confuso, paralisado, congelado)."),
}
SUPREMOS = {
    "ultimo_folego": ("Selo do Último Fôlego", "Uma vez por luta, sobrevive a um golpe fatal com 1 de vida."),
    "vinganca":      ("Selo da Vingança", "Abaixo de 30% de vida, seu dano sobe 25%."),
    "veu_sombrio":   ("Selo do Véu Sombrio", "O primeiro golpe que você recebe na luta causa metade do dano."),
}

# (nome, tags, dano, custo, cd, extras)  |  passivo: (nome, tags, None, efeito)
_R = {
 "Taijutsu": [
  ("Punho Selvagem", [("Soco do Trovão", "Fúria Punho", 1.7, 20, 2, {"status": ("Atordoado", 1, .35)}),
                      ("Chuva de Golpes", "Fúria Velocidade", 2.0, 30, 3, {}),
                      ("Sangue Quente", {"dano": .06}), ("Rugido do Leão", "Fúria Punho", 2.6, 45, 4, {"status": ("Atordoado", 1, .5)}),
                      ("Pele de Aço", {"hp": .10})]),
  ("Passo Fantasma", [("Rasteira Relâmpago", "Velocidade Controle", 1.5, 15, 2, {"status": ("Paralisado", 2, .35)}),
                      ("Dança das Lâminas", "Velocidade Fúria", 1.9, 25, 3, {"furia": 10}),
                      ("Pés Leves", {"esq": .05}), ("Mil Sombras", "Velocidade", 2.5, 40, 4, {"furia": 5}),
                      ("Olhar Afiado", {"crit": .06})]),
  ("Corpo Inquebrável", [("Postura de Pedra", "Defesa Cura", 1.1, 15, 2, {"cura": .06}),
                         ("Golpe Esmagador", "Defesa Punho", 1.9, 30, 3, {}),
                         ("Fôlego de Ferro", {"hp": .08}), ("Último Estandarte", "Defesa Fúria", 2.0, 35, 4, {"cura": .10}),
                         ("Vontade Inabalável", {"dano": .05})]),
 ],
 "Ninjutsu": [
  ("Chamas", [("Bola de Fogo", "Fogo Selo", 1.4, 14, 2, {"status": ("Queimado", 3, .5)}),
              ("Dragão de Chamas", "Fogo", 1.8, 22, 3, {"status": ("Queimado", 3, .7)}),
              ("Brasa Eterna", {"dano": .06}), ("Inferno", "Fogo Selo", 2.2, 34, 4, {"status": ("Queimado", 3, .9)}),
              ("Alma Ardente", {"crit": .05})]),
  ("Tempestade", [("Relâmpago Preciso", "Raio Controle", 1.4, 12, 2, {"status": ("Paralisado", 2, .4)}),
                  ("Corrente de Trovão", "Raio Controle", 1.8, 22, 3, {"dreno": 8}),
                  ("Condutor", {"chakra": .15}), ("Julgamento do Trovão", "Raio", 2.3, 34, 4, {"status": ("Paralisado", 2, .6)}),
                  ("Reflexo Elétrico", {"esq": .05})]),
  ("Mestre dos Selos", [("Selo de Choque", "Selo Controle", 1.3, 10, 2, {"status": ("Atordoado", 1, .3)}),
                        ("Selo Explosivo", "Selo Fogo", 1.8, 24, 3, {}),
                        ("Mãos Rápidas", {"crit": .05}), ("Selo Duplo", "Selo", 1.7, 18, 2, {"buff": 1.2}),
                        ("Mente Fria", {"hp": .08})]),
 ],
 "Genjutsu": [
  ("Névoa", [("Neblina Enganosa", "Ilusão Controle", 1.4, 10, 2, {"status": ("Confusão", 2, .6)}),
             ("Espelho Falso", "Ilusão", 1.8, 18, 3, {"status": ("Confusão", 2, .6)}),
             ("Olhos Hipnóticos", {"crit": .05}), ("Mundo Invertido", "Ilusão Controle", 2.3, 30, 4, {"status": ("Atordoado", 1, .5)}),
             ("Véu Mental", {"esq": .05})]),
  ("Pesadelo", [("Eco do Medo", "Medo Ilusão", 1.3, 12, 2, {"dreno": 10}),
                ("Fome de Chakra", "Medo", 1.7, 20, 3, {"dreno": 14, "sifao": 1}),
                ("Voz Sombria", {"dano": .06}), ("Cem Pesadelos", "Medo Ilusão", 2.0, 32, 4, {"status": ("Confusão", 2, .6), "dreno": 10}),
                ("Calma Absoluta", {"hp": .08})]),
  ("Sombra Aguda", [("Lâmina de Ilusão", "Ilusão Selo", 1.5, 12, 2, {}),
                    ("Duplo Fantasma", "Ilusão", 1.8, 20, 3, {"buff": 1.2}),
                    ("Passo Etéreo", {"esq": .06}), ("Execução Mental", "Medo", 2.1, 36, 4, {}),
                    ("Frieza", {"crit": .06})]),
 ],
 "Marionetista": [
  ("Veneno", [("Agulha Venenosa", "Veneno", 1.2, 10, 2, {"veneno": 1}),
              ("Nuvem Tóxica", "Veneno Controle", 1.5, 18, 3, {"veneno": 1}),
              ("Antídoto Próprio", {"hp": .08}), ("Banquete de Veneno", "Veneno", 2.0, 30, 4, {"veneno": 2}),
              ("Mãos Pacientes", {"dano": .06})]),
  ("Fios", [("Fios de Chakra", "Fio Controle", 1.3, 12, 2, {"status": ("Paralisado", 2, .4)}),
            ("Corte de Fios", "Fio", 2.1, 22, 3, {"status": ("Paralisado", 2, .3)}),
            ("Dedos Ágeis", {"crit": .05}), ("Teia Mortal", "Fio Controle", 2.6, 34, 4, {"status": ("Paralisado", 2, .7)}),
            ("Precisão de Mestre", {"dano": .05})]),
  ("Marionete", [("Reparo Rápido", "Marionete Cura", 1.0, 14, 3, {"escudo": .5}),
                 ("Marionete Gigante", "Marionete", 1.9, 26, 3, {"escudo": .3}),
                 ("Madeira Dura", {"hp": .10}), ("Exército de Bonecos", "Marionete Fio", 2.1, 36, 4, {"escudo": .5}),
                 ("Arsenal Oculto", {"chakra": .15})]),
 ],
 "Kunoichi": [
  ("Bisturi", [("Corte Cirúrgico", "Precisão Cura", 1.7, 12, 2, {"cura": .05}),
               ("Ponto Vital", "Precisão", 2.1, 20, 3, {"status": ("Paralisado", 2, .35)}),
               ("Mãos Firmes", {"crit": .06}), ("Golpe Final", "Precisão", 2.7, 34, 4, {"cura": .08}),
               ("Olho Clínico", {"dano": .06})]),
  ("Cura", [("Chakra Curativo", "Cura", 1.0, 14, 3, {"cura": .12}),
            ("Regeneração", "Cura", 1.2, 22, 4, {"cura": .20}),
            ("Fluxo Vital", {"hp": .10}), ("Renascer Parcial", "Cura", 1.5, 34, 5, {"cura": .30}),
            ("Reserva de Chakra", {"chakra": .15})]),
  ("Tóxico", [("Pó Adormecedor", "Veneno Controle", 1.2, 12, 2, {"status": ("Atordoado", 1, .3)}),
              ("Veneno de Contato", "Veneno", 1.5, 18, 3, {"veneno": 2}),
              ("Resistência a Toxinas", {"hp": .08}), ("Dose Letal", "Veneno Precisão", 2.0, 30, 4, {"veneno": 3}),
              ("Instinto Predador", {"esq": .05})]),
 ],
}


def _montar():
    nos = {}
    for classe, ramos in _R.items():
        for rn, itens in ramos:
            ant = None
            for i, it in enumerate(itens):
                passivo = isinstance(it[1], dict)
                ch = f"{classe[:3].lower()}_{rn[:4].lower().replace(' ', '')}_{i + 1}"
                no = dict(chave=ch, classe=classe, ramo=rn, nome=it[0], pos=i, custo=CUSTO[i], nivel_min=NIVEL_MIN[i], req=ant,
                          passivo=passivo)
                if passivo:
                    no.update(tags=[], efeito=it[1])
                    assert all(v <= PASSIVO_MAX for v in it[1].values()), ch   # regra do GDD 19
                else:
                    _, tags, dano, custo, cd, x = it
                    no.update(tags=tags.split(), dano=dano, custo_uso=custo, cd=cd, x=x)
                nos[ch] = no
                ant = ch
    return nos


NOS = _montar()


def descricao(no):
    if no["passivo"]:
        nomes = {"dano": "dano", "crit": "chance de crítico", "esq": "esquiva", "hp": "vida máxima", "chakra": "chakra máximo"}
        return "Passiva: " + ", ".join(f"+{int(v * 100)}% {nomes[k]}" for k, v in no["efeito"].items())
    ef = []
    for k, v in no["x"].items():
        if k == "status":
            ef.append(f"{int(v[2] * 100)}% de {v[0].lower()}")
        elif k == "veneno":
            ef.append(f"+{v} veneno")
        elif k == "cura":
            ef.append(f"cura {int(v * 100)}% da vida")
        elif k == "dreno":
            ef.append(f"drena {v} chakra")
        elif k == "escudo":
            ef.append(f"repara {int(v * 100)}% da marionete")
        elif k == "buff":
            ef.append("próximo ataque +%d%%" % int((v - 1) * 100))
        elif k == "furia":
            ef.append(f"+{v} fúria")
    uso = "fúria" if no["classe"] == "Taijutsu" else "chakra"
    return f"x{no['dano']} de dano · custa {no['custo_uso']} {uso} · recarga {no['cd']}" + ((" · " + ", ".join(ef)) if ef else "")


def semear(db):
    for n in NOS.values():
        ef = n["efeito"] if n["passivo"] else {"dano": n["dano"], "custo": n["custo_uso"], "cd": n["cd"], "x": n["x"]}
        db.execute("INSERT OR IGNORE INTO skill_nodes(chave,classe,ramo,nome,tags,efeito_json,custo) VALUES (?,?,?,?,?,?,?)",
                   (n["chave"], n["classe"], n["ramo"], n["nome"], " ".join(n["tags"]), json.dumps(ef, ensure_ascii=False), n["custo"]))


# ------------------------------------------------------------------ estado
def slots(nivel):
    return 4 + (nivel >= 30) + (nivel >= 60)


def _estado(db, pid):
    db.execute("INSERT OR IGNORE INTO skill_state(player_id) VALUES (?)", (pid,))
    return db.execute("SELECT respecs, supremo FROM skill_state WHERE player_id=?", (pid,)).fetchone()


def aprendidos(db, pid):
    return {r["node_chave"]: bool(r["equipado"]) for r in db.execute("SELECT node_chave, equipado FROM player_skills WHERE player_id=?", (pid,))}


def pontos_livres(db, p):
    gasto = sum(NOS[k]["custo"] for k in aprendidos(db, p["id"]))
    return p["nivel"] - gasto


def ressonancia(chaves):
    cont = {}
    for k in chaves:
        for t in NOS[k]["tags"]:
            cont[t] = cont.get(t, 0) + 1
    return sorted(t for t, v in cont.items() if v >= 3)


def selos_equipados(db, pid):
    return {r["chave"] for r in db.execute(
        """SELECT s.chave FROM item_selos s JOIN inventory i ON i.id=s.inventory_id
           WHERE i.player_id=? AND i.local='equipado'""", (pid,))}


def aplicar_build(db, l, p):
    """Aplica jutsus equipados, passivas, Selos e Selo Supremo a um Lutador (vale em PvE e nos duelos normalizados)."""
    ap = aprendidos(db, p["id"])
    if p["classe"] != l.classe:
        return l
    for k, eq in ap.items():
        n = NOS.get(k)
        if not n or n["classe"] != l.classe:
            continue
        if n["passivo"]:
            e = n["efeito"]
            l.p_dano *= 1 + e.get("dano", 0)
            l.p_crit += e.get("crit", 0)
            l.p_esq += e.get("esq", 0)
            if e.get("hp"):
                l.hp_max *= 1 + e["hp"]
                l.hp = l.hp_max
                if l.marionete_max:
                    l.marionete_max = l.hp_max * C.MARIONETE_PCT
                    l.marionete = l.marionete_max
            if e.get("chakra") and l.chakra_max:
                l.chakra_max *= 1 + e["chakra"]
                l.chakra = l.chakra_max
        elif eq:
            l.jutsus[k] = dict(nome=n["nome"], dano=n["dano"], custo=n["custo_uso"], cd=n["cd"], x=n["x"], tags=n["tags"])
    if ressonancia(list(l.jutsus)):
        l.ress = RESSONANCIA
    l.selos = selos_equipados(db, p["id"])
    sup = _estado(db, p["id"])["supremo"]
    l.supremo = sup or ""
    return l


# ------------------------------------------------------------------ acoes
def aprender(db, p, chave):
    n = NOS.get(chave)
    if not n or n["classe"] != p["classe"]:
        raise RegraErro("jutsu inválido para sua classe")
    ap = aprendidos(db, p["id"])
    if chave in ap:
        raise RegraErro("você já aprendeu este nó", 409)
    if n["req"] and n["req"] not in ap:
        raise RegraErro(f"aprenda antes: {NOS[n['req']]['nome']}")
    if p["nivel"] < n["nivel_min"]:
        raise RegraErro(f"exige nível {n['nivel_min']}")
    if pontos_livres(db, p) < n["custo"]:
        raise RegraErro("pontos de jutsu insuficientes", 402)
    db.execute("INSERT INTO player_skills(player_id,node_chave,equipado) VALUES (?,?,0)", (p["id"], chave))
    return {"ok": True, "pontos": pontos_livres(db, p)}


def equipar(db, p, chave, ligar=True):
    ap = aprendidos(db, p["id"])
    n = NOS.get(chave)
    if chave not in ap or n["passivo"]:
        raise RegraErro("só jutsus ativos aprendidos podem ser equipados")
    if ligar:
        if sum(1 for k, e in ap.items() if e) >= slots(p["nivel"]) and not ap[chave]:
            raise RegraErro(f"slots cheios ({slots(p['nivel'])})", 409)
    db.execute("UPDATE player_skills SET equipado=? WHERE player_id=? AND node_chave=?", (1 if ligar else 0, p["id"], chave))
    return {"ok": True}


def custo_respec(db, pid):
    r = _estado(db, pid)["respecs"]
    return 0 if r == 0 else RESPEC_BASE * r


def respec(db, p, mover):
    c = custo_respec(db, p["id"])
    if c:
        mover(db, p["id"], "ryos", -c, "respec de jutsus")
    db.execute("DELETE FROM player_skills WHERE player_id=?", (p["id"],))
    db.execute("UPDATE skill_state SET respecs=respecs+1 WHERE player_id=?", (p["id"],))
    return {"ok": True, "custo": c, "pontos": p["nivel"]}


def comprar_selo(db, p, chave, mover):
    if chave not in SELOS:
        raise RegraErro("selo inexistente")
    mover(db, p["id"], "ryos", -PRECO_SELO, f"selo {chave}")
    db.execute("""INSERT INTO player_selos(player_id,chave,qtd) VALUES (?,?,1)
                  ON CONFLICT(player_id,chave) DO UPDATE SET qtd=qtd+1""", (p["id"], chave))
    return {"ok": True}


def encaixar(db, p, inv_id, chave):
    it = db.execute("""SELECT i.id, it.raridade FROM inventory i JOIN items it ON it.id=i.item_id
                       WHERE i.id=? AND i.player_id=? AND it.slot IS NOT NULL""", (inv_id, p["id"])).fetchone()
    if not it:
        raise RegraErro("item inválido", 404)
    if chave not in SELOS:
        raise RegraErro("selo inexistente")
    usados = [r["chave"] for r in db.execute("SELECT chave FROM item_selos WHERE inventory_id=?", (inv_id,))]
    if len(usados) >= SOCKETS[it["raridade"]]:
        raise RegraErro(f"este item só tem {SOCKETS[it['raridade']]} encaixe(s)", 409)
    if chave in usados:
        raise RegraErro("o mesmo selo não acumula no mesmo item", 409)
    est = db.execute("SELECT qtd FROM player_selos WHERE player_id=? AND chave=?", (p["id"], chave)).fetchone()
    if not est or est["qtd"] < 1:
        raise RegraErro("você não tem esse selo", 402)
    db.execute("UPDATE player_selos SET qtd=qtd-1 WHERE player_id=? AND chave=?", (p["id"], chave))
    db.execute("INSERT INTO item_selos(inventory_id,chave) VALUES (?,?)", (inv_id, chave))
    return {"ok": True}


def remover_selo(db, p, inv_id, chave):
    if not db.execute("SELECT 1 FROM inventory WHERE id=? AND player_id=?", (inv_id, p["id"])).fetchone():
        raise RegraErro("item inválido", 404)
    if not db.execute("DELETE FROM item_selos WHERE inventory_id=? AND chave=?", (inv_id, chave)).rowcount:
        raise RegraErro("selo não encontrado", 404)
    db.execute("""INSERT INTO player_selos(player_id,chave,qtd) VALUES (?,?,1)
                  ON CONFLICT(player_id,chave) DO UPDATE SET qtd=qtd+1""", (p["id"], chave))
    return {"ok": True}


def escolher_supremo(db, p, chave, mover):
    if chave not in SUPREMOS:
        raise RegraErro("selo supremo inexistente")
    if ORDEM_PATENTE.index(p["patente"]) < ORDEM_PATENTE.index(PATENTE_SUPREMO):
        raise RegraErro(f"o Selo Supremo exige patente {PATENTE_SUPREMO}")
    atual = _estado(db, p["id"])["supremo"]
    if atual == chave:
        raise RegraErro("você já usa este selo supremo", 409)
    mover(db, p["id"], "ryos", -PRECO_SUPREMO, f"selo supremo {chave}")
    db.execute("UPDATE skill_state SET supremo=? WHERE player_id=?", (chave, p["id"]))
    return {"ok": True}


# ------------------------------------------------------------------ build
def exportar(db, p):
    ap = aprendidos(db, p["id"])
    d = {"c": p["classe"], "n": sorted(ap), "e": sorted(k for k, e in ap.items() if e), "s": _estado(db, p["id"])["supremo"] or ""}
    return "SC1." + base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode()).decode().rstrip("=")


def importar(db, p, codigo):
    """Equipa o que voce ja sabe e informa o que falta aprender (nao gasta pontos sozinho)."""
    try:
        raw = codigo.strip()
        assert raw.startswith("SC1.")
        b = raw[4:]
        d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode())
        assert d["c"] in C.CLASSES and all(k in NOS for k in d["n"])
    except Exception:
        raise RegraErro("código de build inválido")
    if d["c"] != p["classe"]:
        raise RegraErro(f"esta build é de {d['c']}")
    ap = aprendidos(db, p["id"])
    db.execute("UPDATE player_skills SET equipado=0 WHERE player_id=?", (p["id"],))
    equipados, faltam = 0, []
    for k in d["e"]:
        if k in ap and equipados < slots(p["nivel"]):
            db.execute("UPDATE player_skills SET equipado=1 WHERE player_id=? AND node_chave=?", (p["id"], k))
            equipados += 1
    for k in d["n"]:
        if k not in ap:
            faltam.append({"chave": k, "nome": NOS[k]["nome"], "custo": NOS[k]["custo"]})
    return {"equipados": equipados, "faltam": faltam, "supremo": d["s"]}


def estado(db, p):
    ap = aprendidos(db, p["id"])
    st = _estado(db, p["id"])
    ramos = {}
    for n in NOS.values():
        if n["classe"] != p["classe"]:
            continue
        motivo = ""
        if n["chave"] in ap:
            pass
        elif n["req"] and n["req"] not in ap:
            motivo = "aprenda o anterior"
        elif p["nivel"] < n["nivel_min"]:
            motivo = f"nível {n['nivel_min']}"
        ramos.setdefault(n["ramo"], []).append(dict(chave=n["chave"], nome=n["nome"], passivo=n["passivo"], custo=n["custo"],
            nivel_min=n["nivel_min"], tags=n["tags"], desc=descricao(n), aprendido=n["chave"] in ap,
            equipado=bool(ap.get(n["chave"])), bloqueio=motivo))
    eq = [k for k, e in ap.items() if e]
    itens = []
    for r in db.execute("""SELECT i.id, it.nome, it.raridade FROM inventory i JOIN items it ON it.id=i.item_id
                           WHERE i.player_id=? AND it.slot IS NOT NULL ORDER BY i.local DESC, it.nome""", (p["id"],)):
        itens.append(dict(id=r["id"], nome=r["nome"], sockets=SOCKETS[r["raridade"]],
                          selos=[x["chave"] for x in db.execute("SELECT chave FROM item_selos WHERE inventory_id=?", (r["id"],))]))
    return dict(pontos=pontos_livres(db, p), slots=slots(p["nivel"]), usados=len(eq), ramos=[dict(nome=k, nos=v) for k, v in ramos.items()],
                ressonancia=ressonancia(eq), respec_custo=custo_respec(db, p["id"]), supremo=st["supremo"] or "",
                supremos={k: dict(nome=v[0], desc=v[1]) for k, v in SUPREMOS.items()}, preco_supremo=PRECO_SUPREMO,
                selos={k: dict(nome=v[0], desc=v[1]) for k, v in SELOS.items()}, preco_selo=PRECO_SELO,
                estoque={r["chave"]: r["qtd"] for r in db.execute("SELECT chave,qtd FROM player_selos WHERE player_id=? AND qtd>0", (p["id"],))},
                itens=itens, codigo=exportar(db, p))
