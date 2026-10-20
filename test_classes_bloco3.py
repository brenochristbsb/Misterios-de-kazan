"""pytest -q test_classes_bloco3.py  (regras puras do Bloco 3, sem banco)."""
import random

import pytest

import classes_bloco3 as c

U = {"str": 5, "int": 20, "agi": 5, "dex": 10, "vig": 20}


def nova(ativa=True):
    e = c.normalizar({}, U)
    if ativa:
        e, _ = c.invocar(e, 100)
    return e


# ───── HP da marionete ─────
def test_hp_independente_e_normalizado():
    e = c.normalizar({}, U)
    assert e["hp"] == e["hp_max"] == c.hp_max_marionete(U) == 40 + 80 + 100
    assert e["ativa"] is False


def test_normalizar_jogador_antigo_e_dados_sujos():
    e = c.normalizar({"hp": 99999, "modulos": ["fogo", "fogo", "xxx", "agua", "raio", "terra"], "espec": {"combate": 99}}, U)
    assert e["hp"] == e["hp_max"]
    assert e["modulos"] == ["fogo", "agua", "raio"]
    assert e["espec"]["combate"] == c.ESPEC_MAX_POR_RAMO


def test_invocar_valida_chakra_e_estado():
    e = c.normalizar({}, U)
    with pytest.raises(c.RegraErro):
        c.invocar(e, c.MAR_INVOCAR_CUSTO - 1)
    e2, custo = c.invocar(e, 100)
    assert e2["ativa"] and custo == c.MAR_INVOCAR_CUSTO
    with pytest.raises(c.RegraErro):
        c.invocar(e2, 100)


def test_manutencao_sem_chakra_desativa_mas_preserva_hp():
    e = nova()
    e2, pago, caiu = c.manutencao(e, 0)
    assert caiu and not e2["ativa"] and e2["hp"] == e["hp"] and pago == 0
    e3, pago, caiu = c.manutencao(e, 50)
    assert pago == c.MAR_MANUTENCAO_CUSTO and not caiu


# ───── absorção e destruição ─────
def test_absorve_100_por_cento_do_ataque_comum():
    e = nova()
    e2, jogador, na_mar, ev = c.absorver(e, 30)
    assert jogador == 0 and na_mar == 30 and e2["hp"] == e["hp"] - 30 and ev == "protegido"


def test_defesa_da_marionete_reduz_o_dano_que_ela_toma():
    e = nova()
    _, _, sem, _ = c.absorver(e, 100, defesa=0)
    _, _, com, _ = c.absorver(e, 100, defesa=100)
    assert sem == 100 and com == 50


def test_destruicao_desativa_e_excedente_vaza_ao_jogador():
    e = {**nova(), "hp": 10}
    e2, jogador, na_mar, ev = c.absorver(e, 25)
    assert e2["hp"] == 0 and not e2["ativa"] and na_mar == 10 and jogador == 15 and ev == "quebrou"
    # depois de destruída, o jogador volta a receber 100% do dano
    e3, jogador, na_mar, ev = c.absorver(e2, 25)
    assert jogador == 25 and na_mar == 0 and ev == "sem_marionete"


def test_marionete_destruida_nao_pode_ser_reinvocada_ate_recuperar():
    e = {**nova(), "hp": 0, "ativa": False}
    with pytest.raises(c.RegraErro):
        c.invocar(e, 100)
    r = c.recuperar(e, 0.5)
    assert 0 < r["hp"] < r["hp_max"]
    assert c.recuperar(e, 1.0)["hp"] == e["hp_max"]


@pytest.mark.parametrize("prop", ["perfura", "ignora_marionete"])
def test_perfuracao_contorna_sem_dano_na_marionete(prop):
    e = nova()
    e2, jogador, na_mar, ev = c.absorver(e, 40, props=[prop])
    assert jogador == 40 and na_mar == 0 and e2["hp"] == e["hp"] and ev == "contornou"


def test_corte_de_linhas_suspende_protecao_por_dois_turnos():
    e = nova()
    e2, jogador, _, ev = c.absorver(e, 40, props=["rompe_linhas"])
    assert jogador == 40 and ev == "linhas_cortadas" and e2["suspensa"] == c.MAR_LINHAS_CORTADAS_TURNOS
    _, jogador, _, ev = c.absorver(e2, 40)          # ainda suspensa
    assert jogador == 40 and ev == "contornou"
    e3 = c.passar_turno(c.passar_turno(e2))
    assert c.protegendo(e3)
    _, jogador, _, _ = c.absorver(e3, 40)
    assert jogador == 0                              # sem imunidade permanente: volta a proteger


def test_contrajogada_do_jogador_cortar_linhas():
    e = c.cortar_linhas(nova())
    assert not c.protegendo(e)
    assert c.absorver(e, 50)[1] == 50


def test_chefes_e_pvp_tem_ataques_que_desafiam_a_protecao():
    rng = random.Random(1)
    boss = [c.propriedade_ataque_inimigo("pve", True, rng) for _ in range(2000)]
    comum = [c.propriedade_ataque_inimigo("pve", False, rng) for _ in range(500)]
    pvp = [c.propriedade_ataque_inimigo("pvp", False, rng) for _ in range(2000)]
    assert 0.25 < sum(x is not None for x in boss) / 2000 < 0.42
    assert 0.20 < sum(x is not None for x in pvp) / 2000 < 0.40
    assert all(x is None for x in comum)


# ───── veneno ─────
def test_veneno_empilha_com_limite_e_renova_duracao():
    rng = random.Random(0)
    v = None
    for _ in range(9):
        v, ok = c.aplicar_veneno(v, 4, 0.0, rng)
        assert ok
    assert v["stacks"] == c.VENENO_MAX_STACKS and v["turnos"] == c.VENENO_TURNOS


def test_veneno_dano_periodico_expira_e_tem_teto():
    v, _ = c.aplicar_veneno(None, 10, 0.0, random.Random(0))
    total = 0
    for _ in range(10):
        v, d = c.tick_veneno(v, 1000)
        total += d
    assert v is None and total == 10 * c.VENENO_TURNOS
    forte = {"stacks": 5, "dano": 500, "turnos": 3}
    _, d = c.tick_veneno(forte, 1000)
    assert d == 60   # 6% de 1000


def test_veneno_resistencia_e_tratamento():
    rng = random.Random(0)
    assert c.aplicar_veneno(None, 5, 1.0, rng) is not None
    aplicados = sum(c.aplicar_veneno(None, 5, 0.6, random.Random(i))[1] for i in range(1000))
    assert 300 < aplicados < 500
    assert c.tratar_veneno({"stacks": 3, "dano": 5, "turnos": 3}) is None
    assert c.resistencia_veneno(0) == 0 and c.resistencia_veneno(10_000) <= 0.6


# ───── agulhas ─────
def test_agulhas_efeito_debilitante_cresce_com_especializacao():
    base = sum(c.efeito_agulha(0, random.Random(i)) is not None for i in range(2000))
    alto = sum(c.efeito_agulha(5, random.Random(i)) is not None for i in range(2000))
    assert alto > base
    assert {c.efeito_agulha(5, random.Random(i)) for i in range(200)} <= {None, "sangrando", "cego"}


# ───── módulos elementais e especializações ─────
def test_modulos_exigem_instalacao_custo_e_recarga():
    e = nova()
    with pytest.raises(c.RegraErro):
        c.usar_modulo(e, "fogo", 100)                       # não instalado
    e, ryos = c.instalar_modulo(e, "fogo", 500)
    assert ryos == c.MAR_INSTALAR_RYOS
    with pytest.raises(c.RegraErro):
        c.usar_modulo(e, "fogo", 1)                         # sem chakra
    e, custo = c.usar_modulo(e, "fogo", 100)
    assert custo == c.MODULOS["fogo"]["custo"]
    with pytest.raises(c.RegraErro):
        c.usar_modulo(e, "fogo", 100)                       # em recarga
    e = c.passar_turno(e)                                   # fim do turno em que foi usado
    for _ in range(c.MODULOS["fogo"]["recarga"] - 1):
        e = c.passar_turno(e)
    with pytest.raises(c.RegraErro):
        c.usar_modulo(e, "fogo", 100)                       # ainda falta 1 turno
    e = c.passar_turno(e)
    c.usar_modulo(e, "fogo", 100)                           # liberado após `recarga` turnos


def test_cada_elemento_tem_modulo_proprio():
    elems = {m["elem"] for m in c.MODULOS.values() if m["elem"]}
    assert elems == {"fogo", "agua", "vento", "raio", "terra"}
    assert c.MODULOS["fumaca"]["efeito"] == "cego" and c.MODULOS["armadilha"]["efeito"] == "atordoa"


def test_slots_e_ryos_dos_modulos():
    e = nova()
    with pytest.raises(c.RegraErro):
        c.instalar_modulo(e, "fogo", c.MAR_INSTALAR_RYOS - 1)
    for m in ("fogo", "agua", "raio"):
        e, _ = c.instalar_modulo(e, m, 999)
    with pytest.raises(c.RegraErro):
        c.instalar_modulo(e, "terra", 999)
    assert "agua" not in c.remover_modulo(e, "agua")["modulos"]


def test_nao_da_para_maximizar_todas_as_especializacoes():
    e = nova()
    for _ in range(100):
        for ramo in c.ESPECS:
            try:
                e = c.investir_espec(e, ramo, level=100)
            except c.RegraErro:
                pass
    assert sum(e["espec"].values()) == c.ESPEC_MAX_TOTAL
    assert not all(v == c.ESPEC_MAX_POR_RAMO for v in e["espec"].values())
    with pytest.raises(c.RegraErro):
        c.investir_espec(nova(), "combate", level=3)        # sem pontos


def test_especializacao_elemental_barateia_e_fortalece():
    assert c.poder_modulo("fogo", {"elementais": 5}) > c.poder_modulo("fogo", {})
    assert c.custo_modulo("fogo", {"elementais": 5}) < c.custo_modulo("fogo", {})


# ───── cura como ação livre ─────
def test_cura_livre_custa_chakra_e_respeita_vida_maxima():
    ec = c.estado_cura_padrao()
    ec2, cura, custo = c.curar(ec, "palma", 1, 100, 50, 100, 10)
    assert custo == 30 and 0 < cura <= 50
    _, cura, _ = c.curar(c.estado_cura_padrao(), "palma", 1, 100, 99, 100, 10)
    assert cura == 1
    with pytest.raises(c.RegraErro):
        c.curar(ec, "palma", 1, 100, 100, 100, 10)         # vida cheia
    with pytest.raises(c.RegraErro):
        c.curar(ec, "palma", 1, 29, 10, 100, 10)           # sem chakra
    with pytest.raises(c.RegraErro):
        c.curar(ec, "inexistente", 1, 100, 10, 100, 10)


def test_nao_ha_multiplas_curas_no_mesmo_turno():
    ec, _, _ = c.curar(c.estado_cura_padrao(), "palma", 3, 500, 10, 100, 10)
    with pytest.raises(c.RegraErro):
        c.curar(ec, "selo", 3, 500, 10, 100, 10)
    c.curar(ec, "palma", 4, 500, 10, 100, 10)              # no turno seguinte pode


def test_sem_ciclo_infinito_teto_por_luta_e_custo_escalonado():
    ec, gasto = c.estado_cura_padrao(), 0
    for turno in range(1, 50):
        try:
            ec, _, custo = c.curar(ec, "palma", turno, 10_000, 1, 100, 10)
            gasto += custo
        except c.RegraErro:
            break
    assert ec["total"] == c.MAX_CURAS_LUTA
    assert gasto > c.MAX_CURAS_LUTA * c.CURAS["palma"]["custo"]   # custos crescentes


def test_cura_forte_tem_recarga():
    ec, _, _ = c.curar(c.estado_cura_padrao(), "selo", 1, 500, 1, 100, 10)
    with pytest.raises(c.RegraErro):
        c.curar(ec, "selo", 3, 500, 1, 100, 10)
    c.curar(ec, "selo", 1 + c.CURAS["selo"]["recarga"], 500, 1, 100, 10)


def test_cliente_nao_controla_custos():
    # nenhuma função recebe custo/cura do cliente; custos negativos são impossíveis por construção
    assert all(v["custo"] > 0 and v["pct"] > 0 for v in c.CURAS.values())
    assert all(m["custo"] > 0 for m in c.MODULOS.values())


# ───── dano verdadeiro ─────
def test_bisturi_custo_e_teto_do_dano_verdadeiro():
    assert c.custo_bisturi(8) == 12
    assert c.limitar_verdadeiro(10_000, 400) == 100
    assert c.limitar_verdadeiro(30, 400) == 30
    assert c.limitar_verdadeiro(0, 400) == 0                # esquiva continua dano 0


# ───── passivas hospitalares ─────
def test_passiva_hospital_reduz_cooldown_para_5_min():
    assert c.cd_hospital("ninja_medico", "hospital") == 300
    assert c.cd_hospital("ninja_medico", "tratamento") == 600
    assert c.cd_hospital("taijutsu", "hospital") == 600
    assert c.cd_hospital("ninja_medico", None) == 600


def test_passiva_tratamento_reduz_30_por_cento():
    assert c.preco_tratamento(100, "ninja_medico", "tratamento") == 70
    assert c.preco_tratamento(100, "ninja_medico", "hospital") == 100
    assert c.preco_tratamento(100, "ninjutsu", "tratamento") == 100
    assert c.preco_tratamento(1, "ninja_medico", "tratamento") >= 1


def test_escolha_da_passiva_valida_classe_e_unicidade():
    assert c.escolher_passiva("ninja_medico", None, "hospital") == "hospital"
    for args in (("taijutsu", None, "hospital"), ("ninja_medico", None, "x"), ("ninja_medico", "hospital", "tratamento")):
        with pytest.raises(c.RegraErro):
            c.escolher_passiva(*args)
