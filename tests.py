"""Testes automáticos do Shadow Clan RPG. Rodar:  python -m unittest tests -v"""
import hashlib, hmac, json, os, random, sqlite3, tempfile, time, unittest
from datetime import timedelta
from urllib.parse import urlencode

import api, combate_reativo as C, dados as D


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = api.create_app(os.path.join(self.tmp, "t.db"), dev=True)
        self.c = self.app.test_client()
        random.seed(1234)

    def req(self, metodo, url, uid=1, **dados):
        h = {"X-Dev-User": str(uid)}
        r = getattr(self.c, metodo)(url, headers=h, json=dados or None)
        return r.status_code, r.get_json()

    def criar(self, uid=1, nome="Aki", classe="Ninjutsu", sexo="M"):
        _, q = self.req("get", "/api/questionario", uid)
        resp = [p["opcoes"][0]["token"] for p in q["perguntas"]]
        return self.req("post", "/api/personagem", uid, nome=nome, sexo=sexo, classe=classe, respostas=resp)

    def db(self):
        return api.conectar(self.app.config["DB"])

    def luta_completa(self, uid=1):
        for _ in range(60):
            st, e = self.req("post", "/api/combate/turno", uid, postura="Técnica", reacoes=["Esquiva", "Bloqueio"])
            self.assertEqual(st, 200, e)
            if e["terminou"]:
                return e
        self.fail("luta não terminou")


class TestBanco(Base):
    def test_seed(self):
        d = self.db()
        self.assertEqual(d.execute("SELECT count(*) FROM villages").fetchone()[0], 5)
        self.assertEqual(d.execute("SELECT count(*) FROM clans").fetchone()[0], 15)
        self.assertGreater(d.execute("SELECT count(*) FROM items").fetchone()[0], 15)

    def test_reiniciar_nao_apaga_jogadores(self):
        self.criar()
        api.iniciar_banco(self.app.config["DB"])  # regra de ouro: nunca apaga
        self.assertEqual(self.db().execute("SELECT count(*) FROM players").fetchone()[0], 1)

    def test_travas(self):
        self.criar()
        d = self.db()
        with self.assertRaises(sqlite3.DatabaseError):
            d.execute("UPDATE players SET energia=30")
        with self.assertRaises(sqlite3.DatabaseError):
            d.execute("INSERT INTO currency_ledger(player_id,moeda,delta,saldo_apos,motivo,idem_key) VALUES (1,'ryos',1,1,'x','inicio:1')")


class TestAuth(Base):
    def test_sem_header(self):
        self.assertEqual(self.c.get("/api/perfil").status_code, 401)

    def test_init_data(self):
        token = "123:ABC"
        campos = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 77})}
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(campos.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        ok = urlencode({**campos, "hash": h})
        self.assertEqual(api.validar_init_data(ok, token)["id"], 77)
        self.assertIsNone(api.validar_init_data(urlencode({**campos, "hash": "0" * 64}), token))
        self.assertIsNone(api.validar_init_data(ok, "outro:token"))
        velho = {"auth_date": "1000", "user": json.dumps({"id": 1})}
        dv = "\n".join(f"{k}={v}" for k, v in sorted(velho.items()))
        hv = hmac.new(secret, dv.encode(), hashlib.sha256).hexdigest()
        self.assertIsNone(api.validar_init_data(urlencode({**velho, "hash": hv}), token))


class TestPersonagem(Base):
    def test_cinco_classes(self):
        for i, cl in enumerate(C.CLASSES, start=1):
            st, p = self.criar(i, f"Ninja{i}", cl)
            self.assertEqual(st, 201, p)
            self.assertEqual(p["classe"], cl)
            self.assertEqual(p["elemento"] is None, cl == "Taijutsu")

    def test_duplicado_e_nome(self):
        self.criar()
        self.assertEqual(self.criar()[0], 409)
        self.assertEqual(self.criar(2, "Aki")[0], 409)
        self.assertEqual(self.criar(3, "ab")[0], 409)

    def test_respostas_invalidas(self):
        st, _ = self.req("post", "/api/personagem", 1, nome="Zed", sexo="M", classe="Ninjutsu", respostas=["x"] * 5)
        self.assertEqual(st, 400)

    def test_aldeia_menos_populosa_e_marionete(self):
        self.criar(1, "Alfa", "Marionetista")
        self.criar(2, "Beta", "Kunoichi")
        d = self.db()
        self.assertEqual(d.execute("SELECT count(DISTINCT village_id) FROM players").fetchone()[0], 2)
        self.assertEqual(d.execute("SELECT count(*) FROM marionettes").fetchone()[0], 1)

    def test_atributos(self):
        self.criar()
        self.assertEqual(self.req("post", "/api/atributos", 1, pontos={"str": 1})[0], 400)  # sem pontos livres
        self.db().execute("UPDATE players SET pontos_livres=5")
        st, p = self.req("post", "/api/atributos", 1, pontos={"vig": 3, "dex": 2})
        self.assertEqual(st, 200)
        self.assertEqual(p["pontos_livres"], 0)


class TestExploracaoCombate(Base):
    def test_fluxo_completo(self):
        self.criar()
        st, e = self.req("post", "/api/explorar", 1, mapa=1)
        self.assertEqual(st, 200, e)
        self.assertEqual(self.req("post", "/api/explorar", 1, mapa=1)[0], 409)   # já em luta
        _, perfil = self.req("get", "/api/perfil")
        self.assertEqual(perfil["energia"], 24)
        fim = self.luta_completa()
        self.assertTrue(fim["terminou"])
        self.assertIn("recompensa", fim)
        self.assertEqual(len(fim["cronica"]), 5)
        d = self.db()
        self.assertEqual(d.execute("SELECT count(*) FROM active_battles").fetchone()[0], 0)
        self.assertEqual(d.execute("SELECT count(*) FROM battle_logs").fetchone()[0], 1)

    def test_vitoria_paga_no_ledger(self):
        self.criar()
        ganhou = False
        for _ in range(12):
            self.req("post", "/api/explorar", 1, mapa=1)
            fim = self.luta_completa()
            if fim["recompensa"]["venceu"]:
                ganhou = True
                break
            self.req("post", "/api/hospital", 1, tipo="completo")
        self.assertTrue(ganhou)
        d = self.db()
        soma = d.execute("SELECT sum(delta) FROM currency_ledger WHERE moeda='ryos'").fetchone()[0]
        self.assertEqual(soma, d.execute("SELECT ryos FROM players").fetchone()[0])  # ledger bate com o saldo

    def test_sem_energia_e_regeneracao(self):
        self.criar()
        self.db().execute("UPDATE players SET energia=0, energia_em=datetime('now')")
        self.assertEqual(self.req("post", "/api/explorar", 1, mapa=1)[0], 409)
        antes = (api.agora() - timedelta(minutes=13)).strftime(api.FMT)
        self.db().execute("UPDATE players SET energia_em=?", (antes,))
        _, p = self.req("get", "/api/perfil")
        self.assertEqual(p["energia"], 2)   # 13 min / 6 = 2 pontos

    def test_mapa_bloqueado(self):
        self.criar()
        self.assertEqual(self.req("post", "/api/explorar", 1, mapa=3)[0], 403)

    def test_pity_do_boss(self):
        self.criar()
        d = self.db()
        d.execute("INSERT INTO map_threat(player_id,mapa,pontos) VALUES (1,1,100)")
        with api.tx(d):
            self.assertEqual(api.sortear_tier(d, 1, 1), "boss")
        self.assertEqual(d.execute("SELECT pontos FROM map_threat").fetchone()[0], 0)

    def test_decisao_invalida(self):
        self.criar()
        self.req("post", "/api/explorar", 1, mapa=1)
        self.assertEqual(self.req("post", "/api/combate/turno", 1, postura="Voadora")[0], 400)
        self.assertEqual(self.req("post", "/api/combate/turno", 1, postura="Pesada", reacoes=["Esquiva"] * 3)[0], 400)

    def test_fugir_e_treino(self):
        self.criar()
        st, e = self.req("post", "/api/treino", 1, classe="Genjutsu", ambiente="Rio")
        self.assertEqual(st, 200)
        self.assertEqual(self.req("post", "/api/combate/fugir", 1)[0], 200)
        self.assertEqual(self.req("get", "/api/combate", 1)[0], 404)

    def test_boss_anuncia_golpe(self):
        self.criar()
        d = self.db()
        d.execute("INSERT INTO map_threat(player_id,mapa,pontos) VALUES (1,1,100)")
        st, e = self.req("post", "/api/explorar", 1, mapa=1)
        self.assertEqual(e["tier"], "boss")
        self.assertIsNotNone(e["combate"]["anuncio"])


class TestHospitalEquipMercado(Base):
    def test_hospital(self):
        self.criar()
        self.db().execute("UPDATE players SET hp_atual=30, ryos=500")
        self.assertEqual(self.req("post", "/api/hospital", 1, tipo="premium")[0], 200)
        _, p = self.req("get", "/api/perfil")
        self.assertEqual(p["hp"], p["hp_max"])
        self.assertEqual(p["ryos"], 500 - 60 * 1)
        self.req("post", "/api/treino", 1, classe="Taijutsu", ambiente="Rio")
        self.assertEqual(self.req("post", "/api/hospital", 1, tipo="completo")[0], 409)  # bloqueado em luta

    def test_hospital_sem_dinheiro(self):
        self.criar()
        self.db().execute("UPDATE players SET ryos=1, hp_atual=10")
        self.assertEqual(self.req("post", "/api/hospital", 1, tipo="completo")[0], 402)

    def test_equipar_restricoes(self):
        self.criar(1, "Aki", "Ninjutsu")
        d = self.db()
        manopla = d.execute("SELECT id FROM items WHERE chave='manopla_comum'").fetchone()[0]
        d.execute("INSERT INTO inventory(player_id,item_id) VALUES (1,?)", (manopla,))
        inv = d.execute("SELECT max(id) FROM inventory").fetchone()[0]
        self.assertEqual(self.req("post", "/api/equipar", 1, inventory_id=inv)[0], 403)   # exclusivo do Taijutsu

    def test_mercado(self):
        self.criar(1, "Vendedor", "Ninjutsu")
        self.criar(2, "Comprador", "Genjutsu")
        d = self.db()
        iid = d.execute("SELECT id FROM items WHERE chave='fibra'").fetchone()[0]
        amu = d.execute("SELECT id FROM items WHERE chave='amuleto_sorte'").fetchone()[0]
        d.execute("INSERT INTO inventory(player_id,item_id) VALUES (1,?)", (amu,))
        inv = d.execute("SELECT max(id) FROM inventory").fetchone()[0]
        d.execute("UPDATE players SET ryos=200 WHERE id=2")
        # equipado não pode ser anunciado
        eq = d.execute("SELECT id FROM inventory WHERE player_id=1 AND local='equipado'").fetchone()[0]
        self.assertEqual(self.req("post", "/api/mercado/anunciar", 1, inventory_id=eq, preco=50)[0], 409)
        self.assertEqual(self.req("post", "/api/mercado/anunciar", 1, inventory_id=inv, preco=100)[0], 200)
        _, m = self.req("get", "/api/mercado", 2)
        lid = m["anuncios"][0]["id"]
        self.assertEqual(self.req("post", "/api/mercado/comprar", 1, listing_id=lid)[0], 409)  # próprio item
        self.assertEqual(self.req("post", "/api/mercado/comprar", 2, listing_id=lid)[0], 200)
        self.assertEqual(self.req("post", "/api/mercado/comprar", 2, listing_id=lid)[0], 404)  # já vendido
        r = {x[0]: x[1] for x in d.execute("SELECT id, ryos FROM players")}
        self.assertEqual(r[2], 100)                    # pagou 100
        self.assertEqual(r[1], 100 - 10 + 95)          # inicial 100 - taxa 10 + (100 - 5%)
        self.assertEqual(d.execute("SELECT player_id FROM inventory WHERE id=?", (inv,)).fetchone()[0], 2)

    def test_comprar_sem_dinheiro_desfaz_tudo(self):
        self.criar(1, "Vend", "Ninjutsu")
        self.criar(2, "Comp", "Genjutsu")
        d = self.db()
        amu = d.execute("SELECT id FROM items WHERE chave='amuleto_sorte'").fetchone()[0]
        d.execute("INSERT INTO inventory(player_id,item_id) VALUES (1,?)", (amu,))
        inv = d.execute("SELECT max(id) FROM inventory").fetchone()[0]
        self.req("post", "/api/mercado/anunciar", 1, inventory_id=inv, preco=999)
        lid = self.req("get", "/api/mercado", 2)[1]["anuncios"][0]["id"]
        self.assertEqual(self.req("post", "/api/mercado/comprar", 2, listing_id=lid)[0], 402)
        self.assertEqual(d.execute("SELECT player_id FROM inventory WHERE id=?", (inv,)).fetchone()[0], 1)

    def test_ledger_idempotente(self):
        self.criar()
        d = self.db()
        with api.tx(d):
            self.assertTrue(api.mover(d, 1, "ryos", 50, "t", "k-unica"))
            self.assertFalse(api.mover(d, 1, "ryos", 50, "t", "k-unica"))
        self.assertEqual(d.execute("SELECT ryos FROM players").fetchone()[0], 150)


class TestMotor(unittest.TestCase):
    def test_equilibrio_medio(self):
        random.seed(7)
        N, med = 150, {c: [] for c in C.CLASSES}
        for c1 in C.CLASSES:
            for c2 in C.CLASSES:
                if c1 == c2:
                    continue
                w = 0
                for _ in range(N):
                    a, b = C.criar(c1, "A", random.choice(C.ELEMENTOS)), C.criar(c2, "B", random.choice(C.ELEMENTOS))
                    w += C.Batalha(a, b, random.choice(list(C.AMBIENTES)), narrar=False).lutar() is a
                med[c1].append(w / N)
        for c, v in med.items():
            self.assertTrue(0.44 <= sum(v) / len(v) <= 0.56, (c, sum(v) / len(v)))   # meta 47-53% com margem de ruído

    def test_regras_de_classe(self):
        random.seed(3)
        # Marionete absorve 100% do dano direto
        m = C.criar("Marionetista", "M")
        b = C.Batalha(m, C.criar("Taijutsu", "T"), "Floresta")
        hp0, pm0 = m.hp, m.marionete
        b.receber(m, pm0 / 2)
        self.assertEqual(m.hp, hp0)
        self.assertAlmostEqual(m.marionete, pm0 / 2)
        # Taijutsu nunca tem elemento; elemental dobra no Ninjutsu
        self.assertIsNone(C.criar("Taijutsu", "T", "Fogo").elemento)
        self.assertEqual(C.mod_elemental("Ninjutsu", "Fogo", "Vento"), 1.5)
        self.assertEqual(C.mod_elemental("Genjutsu", "Fogo", "Vento"), 1.25)
        self.assertEqual(C.mod_elemental("Taijutsu", "Fogo", "Vento"), 1.0)
        self.assertEqual(C.mod_elemental("Ninjutsu", "Vento", "Fogo"), 0.75)

    def test_kunoichi_dano_verdadeiro(self):
        random.seed(5)
        k, alvo = C.criar("Kunoichi", "K"), C.criar("Taijutsu", "T")
        alvo.attrs["VIG"] = 400  # mitigação enorme
        n = C.Batalha(k, alvo, "Floresta")
        d, _ = n.calc(k, alvo, "Pesada", pode_crit=False)
        ref = C.criar("Ninjutsu", "N")
        d2, _ = n.calc(ref, alvo, "Pesada", pode_crit=False)
        self.assertGreater(d, d2 * 0.6)   # ignora boa parte do Vigor

    def test_narrador_sem_repeticao_e_crônica(self):
        random.seed(9)
        b = C.Batalha(C.criar("Ninjutsu", "A"), C.criar("Genjutsu", "B"), "Chuva")
        v = b.lutar()
        self.assertTrue(b.terminou())
        self.assertEqual(len(b.n.cronica(v, b.rodada)), 5)
        self.assertEqual(b.n.linhas[0], "— Abertura —")
        self.assertEqual(b.n.linhas[-1], "— Desfecho —")


if __name__ == "__main__":
    unittest.main()


# =========================================================== governo e guerra
import governo as G
from datetime import datetime, timedelta

SEXTA_TARDE = datetime(2026, 10, 22, 15, 0, 0)    # quinta, antes da janela
JANELA_SAB = datetime(2026, 10, 25, 0, 0, 0)      # sabado 21h em Brasilia (dentro da janela)
POS_JANELA = datetime(2026, 10, 25, 2, 0, 0)      # sabado 23h em Brasilia (janela encerrada)


class Gov(Base):
    def setUp(self):
        super().setUp()
        self._agora = G.agora

    def tearDown(self):
        G.agora = self._agora

    def relogio(self, dt):
        G.agora = lambda: dt

    def novo(self, uid, nome, classe="Ninjutsu", aldeia=1, pp=0, nivel=10):
        _, q = self.req("get", "/api/questionario", uid)
        resp = [p["opcoes"][0]["token"] for p in q["perguntas"]]
        s, j = self.req("post", "/api/personagem", uid, nome=nome, sexo="M", classe=classe, respostas=resp, aldeia_id=aldeia)
        self.assertEqual(s, 201, j)
        d = self.db()
        d.execute("UPDATE players SET pp_carreira=?, fama=?, nivel=?, patente=? WHERE telegram_id=?",
                  (pp, pp, nivel, G.patente_por_pontos(pp), uid))
        d.close()

    def sql(self, q, *a):
        d = self.db()
        r = d.execute(q, a).fetchall()
        d.close()
        return r

    def kage_de(self, aldeia=1):
        return self.sql("SELECT kage_id FROM thrones WHERE village_id=?", aldeia)[0][0]

    def pid(self, uid):
        return self.sql("SELECT id FROM players WHERE telegram_id=?", uid)[0][0]

    def tirar_escudo(self, aldeia=1):
        self.sql("UPDATE thrones SET escudo_ate=datetime('now','-1 hour') WHERE village_id=?", aldeia)
        d = self.db()
        d.execute("UPDATE thrones SET escudo_ate=datetime('now','-1 hour') WHERE village_id=?", (aldeia,))
        d.close()

    def jogar(self, uid):
        r = None
        for _ in range(80):
            s, r = self.req("post", "/api/combate/turno", uid, postura="Rápida", reacoes=[])
            self.assertEqual(s, 200, r)
            if r["terminou"]:
                return r
        self.fail("luta não terminou")

    def dois_sannins(self, a=101, b=102, aldeia=1, pa=25000, pb=24000):
        self.novo(a, f"Kage{a}", aldeia=aldeia, pp=pa)
        self.novo(b, f"Sannin{b}", aldeia=aldeia, pp=pb)

    # -- patentes e conselho
    def test_patente_por_pontos(self):
        self.assertEqual([G.patente_por_pontos(x) for x in (0, 99, 100, 500, 2000, 6000, 30000)],
                         ["Estudante", "Estudante", "Genin", "Chunin", "Jounin", "Jounin Elite", "Jounin Elite"])

    def test_sannins_sao_os_3_melhores_jounin_elite(self):
        for i, pp in enumerate([9000, 8000, 7000, 6500, 100], start=1):
            self.novo(200 + i, f"Nin{i}", pp=pp)
        d = self.db()
        nomes = [s["nome"] for s in G.sannins(d, 1)]
        d.close()
        self.assertEqual(nomes, ["Nin1", "Nin2", "Nin3"])

    # -- coroacao
    def test_coroar_exige_sannin_e_fama(self):
        self.novo(1, "Fraco", pp=5000)
        self.assertEqual(self.req("post", "/api/trono/coroar", 1)[0], 403)
        self.novo(2, "Quase", pp=19999)
        self.assertEqual(self.req("post", "/api/trono/coroar", 2)[0], 403)

    def test_coroacao_da_titulo_escudo_e_hall(self):
        self.novo(1, "Hanzo", pp=21000)
        s, est = self.req("post", "/api/trono/coroar", 1, discurso="x" * 200)
        self.assertEqual(s, 200, est)
        self.assertEqual(est["kage"]["nome"], "Hanzo")
        self.assertTrue(est["kage"]["escudo_ativo"])
        self.assertTrue(est["eu"]["sou_kage"])
        titulo = self.sql("SELECT titulo_equipado FROM players WHERE telegram_id=1")[0][0]
        self.assertTrue(titulo.startswith("1º Kage de "))
        self.assertEqual(len(self.sql("SELECT discurso FROM hall_of_kages")[0][0]), 140)
        self.assertEqual(self.req("post", "/api/trono/coroar", 1)[0], 403)  # trono já ocupado

    # -- desafio
    def test_regras_do_desafio(self):
        self.dois_sannins()
        self.req("post", "/api/trono/coroar", 101)
        s, e = self.req("post", "/api/trono/desafiar", 102)
        self.assertEqual(s, 403)
        self.assertIn("Escudo", e["erro"])
        self.tirar_escudo()
        self.novo(103, "Fraco", pp=15000)
        self.sql("UPDATE players SET patente='Jounin Elite' WHERE telegram_id=103")
        s, e = self.req("post", "/api/trono/desafiar", 103)
        self.assertEqual(s, 403)
        self.assertIn("20000", e["erro"])
        s, e = self.req("post", "/api/trono/desafiar", 101)  # o Kage não desafia a si mesmo
        self.assertEqual(s, 403)

    def test_desafio_exige_90_por_cento_da_fama_do_kage(self):
        self.dois_sannins(pa=40000, pb=30000)       # 75% da fama do Kage
        self.req("post", "/api/trono/coroar", 101)
        self.tirar_escudo()
        s, e = self.req("post", "/api/trono/desafiar", 102)
        self.assertEqual(s, 403)
        self.assertIn("90%", e["erro"])
        self.sql("UPDATE players SET pp_carreira=36000 WHERE telegram_id=102")
        self.assertEqual(self.req("post", "/api/trono/desafiar", 102)[0], 200)

    def test_desafio_bloqueado_na_janela_de_batalha(self):
        self.dois_sannins()
        self.req("post", "/api/trono/coroar", 101)
        self.tirar_escudo()
        self.relogio(JANELA_SAB)
        s, e = self.req("post", "/api/trono/desafiar", 102)
        self.assertEqual(s, 403)
        self.assertIn("sábado", e["erro"])

    def test_uma_tentativa_a_cada_24h(self):
        self.dois_sannins()
        self.req("post", "/api/trono/coroar", 101)
        self.tirar_escudo()
        self.assertEqual(self.req("post", "/api/trono/desafiar", 102)[0], 200)
        self.assertEqual(self.req("post", "/api/combate/fugir", 102)[0], 200)
        s, e = self.req("post", "/api/trono/desafiar", 102)
        self.assertEqual(s, 403)
        self.assertIn("24", e["erro"])

    def test_duelo_completo_resolve_o_trono_sem_mexer_no_hp_real(self):
        self.dois_sannins()
        self.req("post", "/api/trono/coroar", 101)
        self.tirar_escudo()
        hp0 = self.sql("SELECT hp_atual FROM players WHERE telegram_id=102")[0][0]
        s, r = self.req("post", "/api/trono/desafiar", 102)
        self.assertEqual(s, 200, r)
        fim = self.jogar(102)
        res = fim["recompensa"]["trono"]
        self.assertIn(res, ("conquistado", "defendido"))
        self.assertEqual(self.kage_de(), self.pid(102) if res == "conquistado" else self.pid(101))
        self.assertEqual(self.sql("SELECT hp_atual FROM players WHERE telegram_id=102")[0][0], hp0)
        if res == "conquistado":   # novo Kage ganha escudo de 48 h; o derrotado perde o título
            self.assertEqual(self.req("get", "/api/aldeia", 102)[1]["kage"]["numero"], 2)
            self.assertIsNone(self.sql("SELECT titulo_equipado FROM players WHERE telegram_id=101")[0][0])

    def test_resolver_duelo_vitoria_e_defesa(self):
        self.dois_sannins()
        self.req("post", "/api/trono/coroar", 101)
        self.tirar_escudo()
        d = self.db()
        p = G.__dict__ and d.execute("SELECT * FROM players WHERE telegram_id=102").fetchone()
        ctx = {"village": 1, "kage_id": self.pid(101)}
        d.execute("INSERT INTO throne_challenges(village_id,desafiante_id,kage_id) VALUES (1,?,?)", (p["id"], ctx["kage_id"]))
        self.assertEqual(G.resolver_duelo(d, p, False, ctx)["trono"], "defendido")
        self.assertEqual(G.resolver_duelo(d, p, True, ctx)["trono"], "conquistado")
        self.assertEqual(G.resolver_duelo(d, p, True, ctx)["trono"], "anulado")  # o trono já mudou
        d.close()
        self.assertEqual(self.kage_de(), self.pid(102))

    # -- inatividade e desconfianca
    def test_kage_inativo_perde_poderes_e_depois_o_cargo(self):
        self.dois_sannins()
        self.req("post", "/api/trono/coroar", 101)
        self.sql("UPDATE players SET ultimo_acesso=datetime('now','-8 days') WHERE telegram_id=101")
        k = self.req("get", "/api/aldeia", 102)[1]["kage"]
        self.assertTrue(k["poderes_suspensos"])
        self.sql("UPDATE players SET ultimo_acesso=datetime('now','-15 days') WHERE telegram_id=101")
        est = self.req("get", "/api/aldeia", 102)[1]
        self.assertTrue(est["trono_vago"])
        self.assertIsNone(self.kage_de())
        s, j = self.req("get", "/api/jornal", 102)
        self.assertTrue(any("vago" in x["texto"] for x in j["jornal"]))

    def test_voto_de_desconfianca(self):
        self.novo(1, "Kage", pp=30000)
        self.req("post", "/api/trono/coroar", 1)
        for u in (2, 3, 4):
            self.novo(u, f"Povo{u}", pp=0)
        self.assertEqual(self.req("post", "/api/trono/desconfianca", 2)[0], 403)  # escudo ativo
        self.tirar_escudo()
        self.assertFalse(self.req("post", "/api/trono/desconfianca", 2)[1]["kage_caiu"])
        self.assertFalse(self.req("post", "/api/trono/desconfianca", 3)[1]["kage_caiu"])
        self.assertTrue(self.req("post", "/api/trono/desconfianca", 4)[1]["kage_caiu"])
        self.assertIsNone(self.kage_de())

    # -- playbook
    def test_playbook_validacao_e_decisor(self):
        s, _ = self.req("post", "/api/playbook", 1, regras=[])
        self.assertEqual(s, 404)  # sem personagem
        self.novo(1, "Defensor")
        regra = {"se": {"hp_abaixo": 0.5}, "entao": {"postura": "Pesada", "reacoes": ["Esquiva"]}}
        self.assertEqual(self.req("post", "/api/playbook", 1, regras=[regra])[0], 200)
        self.assertEqual(self.req("post", "/api/playbook", 1, regras=[regra] * 4)[0], 400)
        self.assertEqual(self.req("post", "/api/playbook", 1, regras=[{"se": {"xp": 1}, "entao": {}}])[0], 400)
        eu, rival = C.criar("Ninjutsu", "a"), C.criar("Taijutsu", "b")
        eu.hp = eu.hp_max * 0.3
        d = G.DecisorPlaybook([regra])(eu, rival, None)
        self.assertEqual((d["postura"], d["reacoes"]), ("Pesada", ["Esquiva"]))

    # -- guerra das vilas
    def montar_aldeias(self):
        self.dois_sannins(101, 102, aldeia=1)
        self.dois_sannins(201, 202, aldeia=2)
        self.novo(301, "Soldado", aldeia=1, nivel=12)
        for u in (101, 201):
            self.assertEqual(self.req("post", "/api/trono/coroar", u)[0], 200)

    def test_guerra_so_o_kage_declara_e_conselho_aprova_com_2_de_3(self):
        self.montar_aldeias()
        self.relogio(SEXTA_TARDE)
        self.assertEqual(self.req("post", "/api/guerra/declarar", 102)[0], 403)
        s, r = self.req("post", "/api/guerra/declarar", 101)
        self.assertEqual((s, r["votos_sim"]), (200, 1))
        self.assertEqual(self.req("get", "/api/aldeia", 101)[1]["guerra"]["declaracao"], "pendente")
        self.assertEqual(self.req("post", "/api/guerra/votar", 301)[0], 403)   # só Sannin vota
        self.assertEqual(self.req("post", "/api/guerra/votar", 102, apoiar=True)[1]["votos_sim"], 2)
        self.assertEqual(self.req("get", "/api/aldeia", 101)[1]["guerra"]["declaracao"], "aprovada")

    def test_guerra_nao_declara_com_trono_vago_nem_dentro_da_janela(self):
        self.dois_sannins()
        self.relogio(SEXTA_TARDE)
        self.assertEqual(self.req("post", "/api/guerra/votar", 102)[0], 409)    # trono vago
        self.req("post", "/api/trono/coroar", 101)
        self.relogio(JANELA_SAB)
        self.assertEqual(self.req("post", "/api/guerra/declarar", 101)[0], 409)

    def test_guerra_semana_completa_pontos_dominio_e_bencao(self):
        self.montar_aldeias()
        self.relogio(SEXTA_TARDE)
        for k, s in ((101, 102), (201, 202)):
            self.req("post", "/api/guerra/declarar", k)
            self.req("post", "/api/guerra/votar", s, apoiar=True)
        s, e = self.req("post", "/api/guerra/lutar", 301)
        self.assertEqual(s, 409)                                    # fora da janela
        self.relogio(JANELA_SAB)
        s, r = self.req("post", "/api/guerra/lutar", 301)
        self.assertEqual(s, 200, r)
        hp0 = self.sql("SELECT hp_atual FROM players WHERE telegram_id=301")[0][0]
        fim = self.jogar(301)
        self.assertEqual(self.sql("SELECT hp_atual FROM players WHERE telegram_id=301")[0][0], hp0)
        self.assertEqual(fim["recompensa"]["guerra_pontos"], G.PONTOS_VITORIA if fim["recompensa"]["venceu"] else G.PONTOS_DERROTA)
        g = self.req("get", "/api/aldeia", 301)[1]["guerra"]
        self.assertEqual(g["lutas_minhas"], 1)
        self.assertEqual(g["placar"][0]["aldeia_id"], 1)
        self.relogio(POS_JANELA)
        est = self.req("get", "/api/aldeia", 301)[1]
        self.assertIsNotNone(est["guerra"]["dominio_ate"])
        self.assertEqual(est["bonus_ryos"], 1.15)                    # benção do Kage + domínio
        j = self.req("get", "/api/jornal", 301)[1]["jornal"]
        self.assertTrue(any("venceu a Guerra" in x["texto"] for x in j))

    def test_guerra_exige_aldeia_em_guerra_e_nivel(self):
        self.montar_aldeias()
        self.novo(302, "Novato", aldeia=1, nivel=3)
        self.relogio(JANELA_SAB)
        s, e = self.req("post", "/api/guerra/lutar", 301)
        self.assertEqual(s, 403)                                    # aldeia não declarou guerra


# =========================================================== jutsus, selos e builds
import jutsus as J


class TestJutsus(Base):
    def setUp(self):
        super().setUp()
        self.criar(1, "Aki", "Ninjutsu")
        self.sql("UPDATE players SET nivel=40, ryos=50000, patente='Chunin' WHERE telegram_id=1")

    def sql(self, q, *a):
        d = self.db()
        r = d.execute(q, a).fetchall()
        d.close()
        return r

    def aprender(self, *chaves, uid=1):
        for k in chaves:
            s, r = self.req("post", "/api/jutsus/aprender", uid, chave=k)
            self.assertEqual(s, 200, r)

    def test_regras_do_gdd_19(self):
        # nenhum no passa de +15%
        for n in J.NOS.values():
            if n["passivo"]:
                self.assertTrue(all(v <= .15 for v in n["efeito"].values()), n["chave"])
        # a arvore custa mais do que o jogador ganha ate o nivel 100
        custo_classe = sum(n["custo"] for n in J.NOS.values() if n["classe"] == "Ninjutsu")
        self.assertGreater(custo_classe, 100)
        self.assertEqual([J.slots(n) for n in (1, 29, 30, 59, 60)], [4, 4, 5, 5, 6])

    def test_aprender_exige_cadeia_nivel_e_pontos(self):
        s, e = self.req("post", "/api/jutsus/aprender", 1, chave="nin_cham_2")
        self.assertEqual(s, 400)                      # falta o no anterior
        s, e = self.req("post", "/api/jutsus/aprender", 1, chave="tai_punh_1")
        self.assertEqual(s, 400)                      # outra classe
        self.aprender("nin_cham_1")
        s, e = self.req("post", "/api/jutsus/aprender", 1, chave="nin_cham_1")
        self.assertEqual(s, 409)                      # ja aprendido
        self.sql("UPDATE players SET nivel=2 WHERE telegram_id=1")
        s, e = self.req("post", "/api/jutsus/aprender", 1, chave="nin_cham_2")
        self.assertEqual(s, 400)                      # nivel 15 exigido
        self.sql("UPDATE players SET nivel=16 WHERE telegram_id=1")
        self.aprender("nin_cham_2")                   # 16 - 5 - 7 = 4 pontos livres
        s, e = self.req("post", "/api/jutsus/aprender", 1, chave="nin_cham_3")
        self.assertEqual(s, 402)                      # custa 6

    def test_pontos_e_slots(self):
        self.aprender("nin_cham_1", "nin_cham_2")
        _, est = self.req("get", "/api/jutsus", 1)
        self.assertEqual(est["pontos"], 40 - 5 - 7)
        for k in ("nin_cham_1", "nin_cham_2"):
            s, _ = self.req("post", "/api/jutsus/equipar", 1, chave=k, equipar=True)
            self.assertEqual(s, 200)
        s, e = self.req("post", "/api/jutsus/equipar", 1, chave="nin_cham_3", equipar=True)
        self.assertEqual(s, 400)                      # passiva nao se equipa

    def test_slots_cheios(self):
        self.sql("UPDATE players SET nivel=100 WHERE telegram_id=1")
        ks = ["nin_cham_1", "nin_cham_2", "nin_cham_4", "nin_temp_1", "nin_temp_2", "nin_temp_4", "nin_mest_1"]
        self.aprender("nin_cham_1", "nin_cham_2", "nin_cham_3", "nin_cham_4", "nin_temp_1", "nin_temp_2", "nin_temp_3", "nin_temp_4", "nin_mest_1")
        self.sql("UPDATE players SET nivel=45 WHERE telegram_id=1")   # 5 slots
        for k in ks[:5]:
            self.assertEqual(self.req("post", "/api/jutsus/equipar", 1, chave=k)[0], 200)
        s, e = self.req("post", "/api/jutsus/equipar", 1, chave=ks[5])
        self.assertEqual(s, 409)

    def test_ressonancia_com_tres_tags(self):
        self.assertEqual(J.ressonancia(["nin_cham_1", "nin_cham_2", "nin_cham_4"]), ["Fogo"])
        self.assertEqual(J.ressonancia(["nin_cham_1", "nin_cham_2"]), [])
        d = self.db()
        l = C.criar("Ninjutsu", "A", "Fogo")
        p = d.execute("SELECT * FROM players WHERE telegram_id=1").fetchone()
        d.execute("UPDATE players SET nivel=100 WHERE id=?", (p["id"],))
        p = d.execute("SELECT * FROM players WHERE id=?", (p["id"],)).fetchone()
        for k in ("nin_cham_1", "nin_cham_2", "nin_cham_3", "nin_cham_4"):
            J.aprender(d, p, k)
        for k in ("nin_cham_1", "nin_cham_2", "nin_cham_4"):
            J.equipar(d, p, k)
        J.aplicar_build(d, l, p)
        d.close()
        self.assertEqual(l.ress, J.RESSONANCIA)
        self.assertAlmostEqual(l.p_dano, 1.06)        # passiva Brasa Eterna
        self.assertEqual(len(l.jutsus), 3)

    def test_respec_primeiro_gratis_depois_cobra(self):
        self.aprender("nin_cham_1")
        s, r = self.req("post", "/api/jutsus/respec", 1)
        self.assertEqual((s, r["custo"]), (200, 0))
        self.aprender("nin_cham_1")
        ryos = self.sql("SELECT ryos FROM players WHERE telegram_id=1")[0][0]
        s, r = self.req("post", "/api/jutsus/respec", 1)
        self.assertEqual((s, r["custo"]), (200, 1000))
        self.assertEqual(self.sql("SELECT ryos FROM players WHERE telegram_id=1")[0][0], ryos - 1000)
        self.assertEqual(self.sql("SELECT count(*) FROM player_skills")[0][0], 0)

    def test_selos_comprar_encaixar_e_limites(self):
        s, _ = self.req("post", "/api/selos/comprar", 1, chave="brasa_viva")
        self.assertEqual(s, 200)
        self.assertEqual(self.req("post", "/api/selos/comprar", 1, chave="nao_existe")[0], 400)
        inv = self.sql("SELECT i.id, it.raridade FROM inventory i JOIN items it ON it.id=i.item_id WHERE it.slot IS NOT NULL LIMIT 1")
        self.assertTrue(inv)
        iid = inv[0][0]
        s, e = self.req("post", "/api/selos/encaixar", 1, inventory_id=iid, chave="sede_chakra")
        self.assertEqual(s, 402)                      # nao tem esse selo
        s, e = self.req("post", "/api/selos/encaixar", 1, inventory_id=iid, chave="brasa_viva")
        self.assertEqual(s, 200, e)
        s, e = self.req("post", "/api/selos/encaixar", 1, inventory_id=iid, chave="brasa_viva")
        self.assertIn(s, (402, 409))                  # sem estoque ou ja encaixado
        s, e = self.req("post", "/api/selos/remover", 1, inventory_id=iid, chave="brasa_viva")
        self.assertEqual(s, 200)
        _, est = self.req("get", "/api/jutsus", 1)
        self.assertEqual(est["estoque"]["brasa_viva"], 1)   # o selo volta ao estoque

    def test_selo_so_vale_com_item_equipado(self):
        d = self.db()
        p = d.execute("SELECT * FROM players WHERE telegram_id=1").fetchone()
        d.execute("INSERT INTO item_selos(inventory_id,chave) SELECT id,'sede_chakra' FROM inventory WHERE player_id=? LIMIT 1", (p["id"],))
        eq = d.execute("SELECT count(*) FROM inventory WHERE player_id=? AND local='equipado'", (p["id"],)).fetchone()[0]
        got = J.selos_equipados(d, p["id"])
        d.close()
        self.assertEqual(bool(got), eq > 0 and bool(self.sql("SELECT 1 FROM item_selos s JOIN inventory i ON i.id=s.inventory_id WHERE i.local='equipado'")))

    def test_selo_supremo_exige_patente_e_dinheiro(self):
        self.sql("UPDATE players SET patente='Genin' WHERE telegram_id=1")
        self.assertEqual(self.req("post", "/api/selos/supremo", 1, chave="vinganca")[0], 400)
        self.sql("UPDATE players SET patente='Chunin' WHERE telegram_id=1")
        self.assertEqual(self.req("post", "/api/selos/supremo", 1, chave="vinganca")[0], 200)
        self.assertEqual(self.req("post", "/api/selos/supremo", 1, chave="vinganca")[0], 409)
        self.sql("UPDATE players SET ryos=10 WHERE telegram_id=1")
        self.assertEqual(self.req("post", "/api/selos/supremo", 1, chave="veu_sombrio")[0], 402)

    def test_codigo_de_build_ida_e_volta(self):
        self.aprender("nin_cham_1", "nin_cham_2")
        self.req("post", "/api/jutsus/equipar", 1, chave="nin_cham_1")
        _, est = self.req("get", "/api/jutsus", 1)
        cod = est["codigo"]
        self.assertTrue(cod.startswith("SC1."))
        self.req("post", "/api/jutsus/equipar", 1, chave="nin_cham_1", equipar=False)
        s, r = self.req("post", "/api/build/importar", 1, codigo=cod)
        self.assertEqual((s, r["equipados"], r["faltam"]), (200, 1, []))
        self.assertEqual(self.req("post", "/api/build/importar", 1, codigo="lixo")[0], 400)
        self.criar(2, "Bea", "Taijutsu")
        s, r = self.req("post", "/api/build/importar", 2, codigo=cod)
        self.assertEqual(s, 400)                      # build de outra classe

    def test_jutsu_no_combate_gasta_chakra_e_entra_em_recarga(self):
        d = self.db()
        a = C.criar("Ninjutsu", "A", "Fogo")
        a.jutsus["x"] = dict(nome="Teste", dano=2.0, custo=20, cd=3, x={"status": ("Queimado", 3, 1.0)}, tags=[])
        b = C.criar("Taijutsu", "B")
        bat = C.Batalha(a, b, "Floresta", narrar=True)
        bat.abrir()
        antes = a.chakra
        random.seed(5)
        bat.atacar(a, b, "Técnica", [], "x")
        self.assertLess(a.chakra, antes - 19)
        self.assertEqual(a.cds["x"], 3)
        self.assertTrue(any("Teste" in l for l in bat.n.linhas))
        c = a.chakra
        bat.atacar(a, b, "Técnica", [], "x")          # em recarga: cai na tecnica basica
        self.assertEqual(a.cds["x"], 3)
        d.close()

    def test_jutsu_vazio_forca_tecnica_basica(self):
        a = C.criar("Ninjutsu", "A", "Fogo")
        a.jutsus["x"] = dict(nome="Teste", dano=2.0, custo=20, cd=3, x={}, tags=[])
        bat = C.Batalha(a, C.criar("Taijutsu", "B"), "Floresta", narrar=False)
        bat.abrir()
        bat.atacar(a, bat.ls[1], "Técnica", [], "")
        self.assertEqual(a.cds, {})

    def test_supremos_no_motor(self):
        a, b = C.criar("Taijutsu", "A"), C.criar("Ninjutsu", "B", "Fogo")
        a.supremo = "ultimo_folego"
        bat = C.Batalha(a, b, "Floresta", narrar=True)
        bat.receber(a, a.hp_max * 5)
        self.assertEqual(a.hp, 1)
        bat.receber(a, 10)
        self.assertLessEqual(a.hp, 0)                 # so uma vez por luta
        c = C.criar("Taijutsu", "C")
        c.supremo = "veu_sombrio"
        bat.receber(c, 100)
        self.assertAlmostEqual(c.hp_max - c.hp, 50)

    def test_decisao_do_jogador_envia_jutsu_pela_api(self):
        self.aprender("nin_cham_1")
        self.req("post", "/api/jutsus/equipar", 1, chave="nin_cham_1")
        s, r = self.req("post", "/api/explorar", 1, mapa=1)
        self.assertEqual(s, 200, r)
        self.assertEqual([j["chave"] for j in r["combate"]["jogador"]["jutsus"]], ["nin_cham_1"])
        s, e = self.req("post", "/api/combate/turno", 1, postura="Técnica", reacoes=[], jutsu="nin_cham_1")
        self.assertEqual(s, 200, e)
        self.assertTrue(any("Bola de Fogo" in l for l in e["log"]) or e["jogador"]["jutsus"][0]["cd"] >= 0)

    def test_todos_os_nos_seedados_no_banco(self):
        self.assertEqual(self.sql("SELECT count(*) FROM skill_nodes")[0][0], len(J.NOS))
        self.assertEqual(len(J.NOS), 75)
