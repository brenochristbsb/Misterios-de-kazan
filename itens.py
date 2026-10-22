"""Catálogo de itens, gerado por faixas de nível (1, 10, 20 ... 100).

Para trocar o ícone por uma imagem de verdade, adicione ao item o campo "imagem": "https://..."
(o jogo mostra a imagem no lugar do emoji).
"""
RARIDADES = [
    {"id": "comum", "nome": "Comum", "cor": "#9aa4b2"},
    {"id": "incomum", "nome": "Incomum", "cor": "#4ade80"},
    {"id": "raro", "nome": "Raro", "cor": "#4aa8ff"},
    {"id": "epico", "nome": "Épico", "cor": "#b46bff"},
    {"id": "lendario", "nome": "Lendário", "cor": "#f4c95d"},
]
MAX_MELHORIA = 3
NIVEIS = [1] + list(range(10, 101, 10))
MATERIAIS = ["Madeira", "Bronze", "Ferro", "Prata", "Aço", "Cristal", "Obsidiana", "Mithril", "Éter", "Estrelas", "Divindade"]
TAMANHOS = ["Mínima", "Pequena", "Pequena", "Média", "Média", "Grande", "Grande", "Maior", "Superior", "Suprema", "Divina"]
PETS = [("Gatinho da Névoa", "🐱"), ("Raposa Lunar", "🦊"), ("Lobo Cinzento", "🐺"), ("Coruja Sábia", "🦉"),
        ("Pantera Sombria", "🐆"), ("Grifo Menor", "🦅"), ("Fênix Jovem", "🔥"), ("Dragão Pequeno", "🐲"),
        ("Quimera Etérea", "🦁"), ("Unicórnio Estelar", "🦄"), ("Serafim Dourado", "👼")]
ARMAS = [("Cajado", "🪄"), ("Lâmina", "🗡️")]
ARMADURAS = [("Manto", "🥋"), ("Armadura", "🛡️")]
AMULETOS = [("Amuleto", "📿"), ("Anel", "💍")]
ORDEM = {"arma": 0, "armadura": 1, "amuleto": 2, "pet": 3, "consumivel": 4}


def _5(v):
    return max(5, int(round(v / 5.0)) * 5)


def _gerar():
    itens = []
    for t, lv in enumerate(NIVEIS):
        base_r = min(4, t // 2)
        p = 25 * (1 + lv) ** 1.25            # preço-base de equipamento neste nível
        cp = 12 * (1 + lv) ** 1.15           # preço-base de consumível
        for v in (0, 1):                      # variante 0 = normal, 1 = uma raridade acima (+40% atributos)
            r, m, pm = min(4, base_r + v), (1.0, 1.4)[v], (1.0, 2.2)[v]

            def add(cat, nome, icone, stats, bonus, mult_preco):
                itens.append({"id": f"{cat}_{lv}_{v}", "nome": nome, "icone": icone, "categoria": cat, "tipo": "equip",
                              "slot": cat, "raridade": RARIDADES[r]["id"], "nivel_min": lv,
                              "preco": _5(p * pm * mult_preco), "stats": stats, "bonus": bonus})

            b = round((4 + 1.2 * t) * m, 1)
            n, i = ARMAS[v]
            add("arma", f"{n} de {MATERIAIS[t]}", i, {"ataque": round((5 + lv * 3) * m)}, {"exp": b}, 1.0)
            n, i = ARMADURAS[v]
            add("armadura", f"{n} de {MATERIAIS[t]}", i,
                {"defesa": round((4 + lv * 2.5) * m), "vida": round((20 + lv * 10) * m)}, {"moedas": b}, 1.0)
            n, i = AMULETOS[v]
            add("amuleto", f"{n} de {MATERIAIS[t]}", i, {"vida": round((10 + lv * 5) * m)},
                {"eter": round((1 + 0.4 * t) * m, 1)}, 0.8)
            n, i = PETS[t]
            pb = round((2 + 0.6 * t) * m, 1)
            add("pet", n + (" Ancestral" if v else ""), i,
                {"ataque": round((3 + lv * 1.5) * m), "defesa": round((2 + lv * 1.2) * m)}, {"exp": pb, "moedas": pb}, 1.3)
        for k, nome, icone, ef, mult in (
            ("pocao_energia", f"Poção de Energia {TAMANHOS[t]}", "🧪", {"energia": min(25, 5 + t * 2)}, 1.0),
            ("elixir_exp", f"Elixir de Sabedoria {TAMANHOS[t]}", "🔮", {"exp": round(30 * (1 + lv / 10))}, 1.2),
        ):
            itens.append({"id": f"{k}_{lv}", "nome": nome, "icone": icone, "categoria": "consumivel", "tipo": "consumivel",
                          "raridade": RARIDADES[base_r]["id"], "nivel_min": lv, "preco": _5(cp * mult), "stats": {}, "efeito": ef})
    itens.sort(key=lambda x: (x["nivel_min"], ORDEM[x["categoria"]], x["preco"]))
    return {x["id"]: x for x in itens}


ITENS = _gerar()


def custo_melhoria(item, nivel):
    return item["preco"] * (nivel + 1) // 2
