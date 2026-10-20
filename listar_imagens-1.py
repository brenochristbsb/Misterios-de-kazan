#!/usr/bin/env python3
"""
listar_imagens.py — Lista todas as imagens que o app procura e diz quais ainda faltam.

Uso (na pasta do projeto, onde estão game_extra.py, vilas_dados.py, vilas_mapas_extra.py e a pasta img/):
    python listar_imagens.py            # imprime o resumo e grava imagens_necessarias.csv
    python listar_imagens.py --faltam   # lista só o que ainda não existe

O app troca o emoji pela imagem sozinho quando o arquivo existe (e volta ao emoji se não existir), então dá para ir
adicionando aos poucos. Os nomes de arquivo seguem a mesma regra do index.html (sem acento, minúsculo, hífen no lugar de
espaço). Não importa o game_extra (não abre banco nem sobe o servidor): lê os dados como texto.
"""
import ast
import csv
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ESTILO = ("dark fantasy ninja RPG game art, Japanese sumi-e ink painting with muted dark palette (charcoal, deep red, aged gold accents), "
          "dramatic rim lighting, soft vignette, no text, no watermark, no frame, centered subject, square 1:1")
PLURAL = {"Ícone": "Ícones", "Chefe": "Chefes", "Inimigo": "Inimigos", "Item": "Itens"}
TAM = {"Ícone": "512x512", "Inimigo": "512x512", "Chefe": "512x512", "Item": "256x256"}
IMG = RAIZ / "img"


def slug(s: str) -> str:  # igual ao slug() do index.html
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def literais(arquivo: Path, nomes: set) -> dict:
    """Lê do código as variáveis que são literais puros (listas, dicts, textos), sem executar nada."""
    arv, out = ast.parse(arquivo.read_text(encoding="utf-8")), {}
    for no in arv.body:
        try:
            if isinstance(no, ast.Assign) and len(no.targets) == 1 and isinstance(no.targets[0], ast.Name) and no.targets[0].id in nomes:
                out[no.targets[0].id] = ast.literal_eval(no.value)
            elif (isinstance(no, ast.Expr) and isinstance(no.value, ast.Call) and isinstance(no.value.func, ast.Attribute)
                  and no.value.func.attr == "update" and isinstance(no.value.func.value, ast.Name) and no.value.func.value.id in nomes):
                out.setdefault(no.value.func.value.id, {}).update(ast.literal_eval(no.value.args[0]))
        except Exception:
            pass
    return out


def main():
    faltam_so = "--faltam" in sys.argv
    ge = literais(RAIZ / "game_extra.py", {"_RAW_MAPS", "_EXTRAS_ANTIGOS", "_NOVOS_MAPAS", "_NOMES", "FORJA_BASE", "FORJA_SUFIXO"})
    linhas = []  # (categoria, nome, arquivo, descrição para gerar a imagem)

    def add(cat, nome, pasta, prompt):
        linhas.append((cat, nome, f"img/{pasta}{slug(nome)}.jpg", prompt))

    # 1) Ícones do Menu e do Perfil (ids vêm do index.html)
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    for id_, nome in [(i, ("Menu" if i == "menu" else n)) for i, n in re.findall(r"\['([a-z0-9]+)', '(?:[^']{1,8})', '([^']+)', ", html)]:
        linhas.append(("Ícone", nome, f"img/{id_}.jpg", f"emblem icon for the game menu entry '{nome}', a single symbolic object on a dark background"))

    # 2) Inimigos: o comum, os extras e o chefe de cada mapa (nomes genéricos + os de cada vila)
    inimigos = {}
    for m in ge.get("_RAW_MAPS", []):
        inimigos[m[7]] = "Chefe"
    for mid, (extras, _) in ge.get("_EXTRAS_ANTIGOS", {}).items():
        for n in extras:
            inimigos[n] = "Inimigo"
    for m in ge.get("_NOVOS_MAPAS", []):
        inimigos[m[7]], inimigos[m[8]] = "Chefe", "Inimigo"
        for n in m[9]:
            inimigos[n] = "Inimigo"
    try:
        from vilas_mapas_extra import MAPAS_VILA_EXTRA
        for lista in MAPAS_VILA_EXTRA.values():
            for _, ini, chefe, _ in lista:
                inimigos[ini], inimigos[chefe] = "Inimigo", "Chefe"
    except ImportError:
        print("Aviso: vilas_mapas_extra.py não encontrado.")
    try:
        from vilas_dados import VILAS
        for v in VILAS:
            for _, ini, chefe in v["mapas"]:
                inimigos[ini], inimigos[chefe] = "Inimigo", "Chefe"
    except Exception as e:
        print(f"Aviso: não li vilas_dados.py ({e}); faltam os inimigos dos mapas 1 a 5 de cada vila.")
    for nome, tipo in sorted(inimigos.items()):
        add(f"{tipo} de mapa", nome, "inimigos/", f"portrait of '{nome}', {'an imposing boss creature or warrior, menacing, larger than life' if tipo == 'Chefe' else 'a hostile enemy character or creature, waist-up'}, facing the viewer")

    # 3) Itens: drops dos mapas, equipamentos da loja e da forja
    itens = set()
    for m in ge.get("_RAW_MAPS", []):
        itens.update((i[0], m[5] if len(i) < 3 else i[2]) for i in m[6])
    for _, it in ge.get("_EXTRAS_ANTIGOS", {}).values():
        itens.add((it[0], it[2]))
    for m in ge.get("_NOVOS_MAPAS", []):
        itens.update((i[0], m[5] if len(i) < 3 else i[2]) for i in m[6])
    for tipo, nomes in ge.get("_NOMES", {}).items():
        itens.update((n, tipo) for n in nomes)
    base, suf = ge.get("FORJA_BASE", {}), ge.get("FORJA_SUFIXO", [])
    for tipo, b in base.items():
        itens.update((f"{b} {s}", tipo) for s in suf)
    for nome, tipo in sorted(itens):
        add(f"Item ({tipo})", nome, "itens/", f"item icon: '{nome}' ({tipo}), a single object isolated on a dark background, subtle golden glow")

    # 4) Confere o que já existe
    vistos, saida = set(), []
    for cat, nome, arq, prompt in linhas:
        if arq in vistos:
            continue
        vistos.add(arq)
        saida.append((cat, nome, arq, (RAIZ / arq).exists(), prompt))
    tem = sum(1 for x in saida if x[3])
    with open(RAIZ / "imagens_necessarias.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["categoria", "nome", "arquivo", "ja_existe", "sugestao_de_prompt"])
        for cat, nome, arq, ok, prompt in saida:
            w.writerow([cat, nome, arq, "sim" if ok else "não", prompt])
    grupos = {}
    for cat, nome, arq, ok, prompt in saida:
        if not ok:
            grupos.setdefault(cat.split(" (")[0].replace(" de mapa", ""), []).append((nome, arq, prompt))
    with open(RAIZ / "prompts_imagens.md", "w", encoding="utf-8") as f:
        f.write("# Prompts das imagens que faltam\n\n**Como usar:** cole o ESTILO no início de cada pedido e depois a linha do item. "
                "Salve cada imagem com o nome de arquivo indicado, em JPG, dentro da pasta do projeto. Os nomes entre aspas ficam em português "
                "de propósito: serve de referência para o gerador e para você.\n\n"
                f"## ESTILO (igual para todas)\n\n`{ESTILO}`\n\n")
        for g in ("Ícone", "Chefe", "Inimigo", "Item"):
            if g in grupos:
                f.write(f"## {PLURAL[g]} ({len(grupos[g])}) — tamanho sugerido {TAM[g]}\n\n")
                for nome, arq, prompt in grupos[g]:
                    f.write(f"- `{arq}`: {prompt}\n")
                f.write("\n")
    por_cat = {}
    for cat, _, _, ok, _ in saida:
        t = por_cat.setdefault(cat.split(" (")[0], [0, 0])
        t[0] += 1
        t[1] += ok
    print(f"{len(saida)} imagens no total, {tem} já existem, {len(saida) - tem} faltam. Lista em imagens_necessarias.csv e prompts em prompts_imagens.md\n")
    for cat, (n, ok) in sorted(por_cat.items()):
        print(f"  {cat}: {ok}/{n}")
    if faltam_so:
        print()
        for cat, nome, arq, ok, _ in saida:
            if not ok:
                print(f"  {arq}   ({nome})")


if __name__ == "__main__":
    main()
