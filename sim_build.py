import random, sys
import combate_reativo as C, jutsus as J
def build(l, ramos=(0,1,2)):
    ap=[]
    rs=[r for r in J.NOS.values() if r["classe"]==l.classe]
    byr={}
    for n in rs: byr.setdefault(n["ramo"],[]).append(n)
    nomes=list(byr)
    escolha=[]
    r0=byr[nomes[ramos[0]]]; r1=byr[nomes[ramos[1]]]
    escolha=r0+[r1[0]]
    for n in escolha:
        if n["passivo"]:
            e=n["efeito"]; l.p_dano*=1+e.get("dano",0); l.p_crit+=e.get("crit",0); l.p_esq+=e.get("esq",0)
            if e.get("hp"): l.hp_max*=1+e["hp"]; l.hp=l.hp_max
            if e.get("chakra") and l.chakra_max: l.chakra_max*=1+e["chakra"]; l.chakra=l.chakra_max
            if l.marionete_max: l.marionete_max=l.hp_max*C.MARIONETE_PCT; l.marionete=l.marionete_max
        else:
            l.jutsus[n["chave"]]=dict(nome=n["nome"],dano=n["dano"],custo=n["custo_uso"],cd=n["cd"],x=n["x"],tags=n["tags"])
    return l
def sim(N, com=True, ramos=(0,1,2)):
    res={}
    for c1 in C.CLASSES:
        tot=[]
        for c2 in C.CLASSES:
            if c1==c2: continue
            w=0
            for _ in range(N):
                a=C.criar(c1,"A",random.choice(C.ELEMENTOS)); b=C.criar(c2,"B",random.choice(C.ELEMENTOS))
                if com: build(a,ramos); build(b,ramos)
                v=C.Batalha(a,b,random.choice(list(C.AMBIENTES)),narrar=False).lutar(); w+=v is a
            tot.append(w/N)
        res[c1]=sum(tot)/len(tot)
    return res
if __name__=="__main__":
    N=int(sys.argv[1]) if len(sys.argv)>1 else 150
    for r in [(0,1,2),(1,2,0),(2,0,1)]:
        print(r,{k[:5]:f"{v:.0%}" for k,v in sim(N,True,r).items()})
