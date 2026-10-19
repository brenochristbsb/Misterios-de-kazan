"""Clãs do Shadow Clan RPG: história, especialidade, requisitos, provação de ingresso e relíquia do líder.

Só dados, sem imports do jogo (o seed em api.py lê CLANS).

Etapas da provação (separadas por espaço):
  kM x N  = vencer N lutas no mapa M (ex.: k1x8)    bM = derrotar o chefe do mapa M
  d       = duelo com o líder do clã (turnos)       fN = ter N de fama      tN = pagar N ryos de tributo
"""

# (índice da vila, nome, atributo, bônus, nível mínimo, rank mínimo, história, etapas,
#  líder NPC, técnica secreta, relíquia, tipo da relíquia, bônus da relíquia)
CLANS = [
    # 0 · Vila das Chamas Eternas
    (0, "Clã Brasa Rubra", "ninjutsu", 0.15, 5, "Genin",
     "Ferreiros que forjaram a primeira lâmina da vila com o fogo de um vulcão adormecido. Para eles, toda técnica nasce de uma brasa que se recusa a apagar.",
     "k1x8 b1 t300 k2x6 f40 d", "Mestre Kaen Daruma", "Erupção Rubra", "Lâmina do Vulcão", "Arma", {"ninjutsu": 12, "forca": 6}),
    (0, "Clã Punho de Cinza", "taijutsu", 0.15, 6, "Genin",
     "Monges guerreiros que socam colunas de pedra aquecida até virarem cinza. Dizem que o golpe perfeito nunca faz barulho.",
     "k1x10 b1 k2x8 t400 f60 d", "Abade Hidaka", "Punho do Fim da Chama", "Luvas de Cinzas Eternas", "Arma", {"taijutsu": 12, "defesa": 6}),
    (0, "Clã Fênix Pálida", "vigor", 0.15, 7, "Genin",
     "Curandeiros que renasceram de um incêndio que consumiu o antigo templo. Cada geração jura nunca deixar um aliado cair duas vezes.",
     "k1x8 k2x8 b2 t500 f80 d", "Anciã Hoka", "Renascer da Fênix", "Manto da Fênix Pálida", "Armadura", {"vigor": 12, "defesa": 8}),
    # 1 · Vila da Névoa Oculta
    (1, "Clã Véu Cego", "genjutsu", 0.20, 6, "Genin",
     "Ilusionistas que abrem mão da própria visão para enxergar o que a névoa esconde. Ninguém sabe quantos deles já estiveram ao seu lado.",
     "k1x6 b1 k2x10 t400 f70 b2 d", "Mestra Sayo Vazio", "Pesadelo da Névoa", "Máscara do Olho Cego", "Armadura", {"genjutsu": 12, "inteligencia": 8}),
    (1, "Clã Lâmina Sussurrante", "destreza", 0.15, 8, "Genin",
     "Assassinos por contrato que só aceitam alvos condenados pela vila. A lâmina deles nunca é vista, apenas lembrada.",
     "k2x8 b2 t500 k2x6 f90 d", "Mestre Koji Sem Nome", "Corte Sussurrado", "Adaga do Silêncio", "Arma", {"destreza": 14, "agilidade": 6}),
    (1, "Clã Mente de Orvalho", "inteligencia", 0.20, 7, "Genin",
     "Estrategistas que planejam batalhas inteiras antes da primeira gota de orvalho cair. Para eles, perder um membro é admitir erro de cálculo.",
     "k1x8 b1 k2x8 t300 f60 d", "Estrategista Renka", "Xeque-mate Gélido", "Capa de Orvalho Eterno", "Armadura", {"inteligencia": 12, "chakra_attr": 8}),
    # 2 · Vila do Vento Prateado
    (2, "Clã Asa de Prata", "agilidade", 0.15, 5, "Genin",
     "Mensageiros que cruzaram mil montanhas numa só noite. Aprendem a correr antes de aprender a falar.",
     "k1x10 b1 t250 k2x6 f40 d", "Veloz Tamaki", "Rajada Prateada", "Botas do Vento Prateado", "Armadura", {"agilidade": 14, "destreza": 6}),
    (2, "Clã Flecha Gêmea", "destreza", 0.15, 8, "Genin",
     "Arqueiros que disparam duas flechas como se fossem uma. A regra do clã é simples: nunca errar o mesmo alvo duas vezes.",
     "k2x8 b2 t450 k2x8 f80 d", "Arqueira Sen", "Chuva Gêmea", "Arco da Segunda Flecha", "Arma", {"destreza": 14, "taijutsu": 8}),
    (2, "Clã Sopro Antigo", "chakra_attr", 0.20, 9, "Genin",
     "Guardiões de uma respiração ancestral que amplia o chakra a cada tempestade. Dizem que o vento guarda o nome de todos os mortos.",
     "k2x8 b2 k3x6 t600 f100 d", "Velho Tomé do Vento", "Tempestade Ancestral", "Manto do Último Sopro", "Armadura", {"chakra_attr": 14, "ninjutsu": 8}),
    # 3 · Vila da Maré Profunda
    (3, "Clã Concha Abissal", "defesa", 0.20, 6, "Genin",
     "Mergulhadores que desceram onde a luz não alcança e voltaram com armaduras de casco vivo. Nenhuma onda os derruba.",
     "k1x8 b1 k2x8 t350 f60 d", "Capitã Marena", "Muralha da Maré", "Casco do Abismo", "Armadura", {"defesa": 14, "vigor": 8}),
    (3, "Clã Corrente Viva", "ninjutsu", 0.15, 8, "Genin",
     "Pescadores que dominam as correntes e as transformam em lâminas de água. Contam que o mar escolhe quem pode aprender.",
     "k2x8 b2 t450 k2x6 f80 d", "Mestre Uzio", "Redemoinho Cortante", "Tridente da Corrente", "Arma", {"ninjutsu": 13, "agilidade": 7}),
    (3, "Clã Lança do Recife", "forca", 0.15, 7, "Genin",
     "Guerreiros que cresceram arremessando lanças contra tubarões. A cicatriz mais antiga do clã tem trezentos anos.",
     "k1x10 b1 k2x8 t400 f70 d", "Arpoador Gorun", "Arremesso do Recife", "Lança de Trezentos Anos", "Arma", {"forca": 14, "taijutsu": 8}),
    # 4 · Vila do Trovão Dourado
    (4, "Clã Raio Ouro", "taijutsu", 0.15, 7, "Genin",
     "Lutadores que acumulam trovões nos punhos durante as tempestades. Um golpe deles soa como o céu rachando.",
     "k1x10 b1 k2x8 t400 f70 d", "Mestre Raiga", "Punho do Trovão", "Braçadeiras do Raio Dourado", "Arma", {"taijutsu": 13, "forca": 7}),
    (4, "Clã Tambor do Céu", "forca", 0.20, 9, "Genin",
     "Guerreiros que marcham ao ritmo de tambores gigantes e lutam a cada batida. Disciplina de ferro, orgulho de ouro.",
     "k2x8 b2 t500 k3x6 f100 d", "General Daiko", "Batida da Guerra", "Martelo do Tambor Celeste", "Arma", {"forca": 15, "vigor": 7}),
    (4, "Clã Faísca Mestra", "ninjutsu", 0.15, 8, "Genin",
     "Estudiosos que decifram a eletricidade em cada pedra do vale. Já acenderam uma cidade inteira com um único selo.",
     "k2x8 b2 t450 k2x8 f80 d", "Sábia Faíska", "Cadeia de Relâmpagos", "Selo da Cidade Acesa", "Armadura", {"ninjutsu": 12, "inteligencia": 8}),
    # 5 · Vila da Montanha de Jade
    (5, "Clã Muralha de Jade", "defesa", 0.20, 6, "Genin",
     "Pedreiros que moldam jade em muralhas e armaduras. Em toda a sua história, nunca atacaram primeiro.",
     "k1x8 b1 k2x8 t350 f60 d", "Guarda-mor Hekan", "Muralha Quebrada", "Armadura de Jade Viva", "Armadura", {"defesa": 15, "vigor": 7}),
    (5, "Clã Raiz Eterna", "vigor", 0.15, 8, "Genin",
     "Herboristas que ligam o corpo às raízes da montanha. Nenhum deles jamais caiu de fome ou de cansaço.",
     "k2x8 b2 t450 k2x6 f80 d", "Anciã Thara", "Seiva da Montanha", "Cajado da Raiz Eterna", "Arma", {"vigor": 13, "chakra_attr": 8}),
    (5, "Clã Olho de Pedra", "inteligencia", 0.15, 9, "Genin",
     "Vigias que enxergam a montanha inteira num só olhar de pedra. Registram cada passo de cada viajante desde a fundação da vila.",
     "k2x8 b2 t500 k3x6 f100 d", "Vigia Orun", "Olhar Petrificante", "Amuleto do Olho de Pedra", "Armadura", {"inteligencia": 13, "defesa": 8}),
    # 6 · Vila da Lua Cinzenta
    (6, "Clã Sombra Lunar", "genjutsu", 0.15, 6, "Genin",
     "Ilusionistas que só agem sob a lua cheia. Guardam o segredo de uma noite que durou três dias.",
     "k1x8 b1 k2x8 t350 f60 d", "Dama Selene Vahl", "Noite Sem Fim", "Véu da Lua Cheia", "Armadura", {"genjutsu": 13, "agilidade": 7}),
    (6, "Clã Guardiões do Selo", "chakra_attr", 0.20, 9, "Genin",
     "Selaram uma criatura sob a vila e juraram vigiá-la por gerações. O selo se alimenta do chakra deles, e eles aprenderam a se alimentar de volta.",
     "k2x8 b2 k3x6 t500 f100 d", "Selador-mor Ibu", "Selo Devorador", "Chave do Selo Vivo", "Arma", {"chakra_attr": 14, "inteligencia": 8}),
    (6, "Clã Corvo Pálido", "agilidade", 0.15, 7, "Genin",
     "Espiões que se disfarçam de corvos nas ruínas da fronteira. Todo segredo de guerra passa primeiro por suas asas.",
     "k1x10 b1 k2x8 t400 f70 d", "Mestre Corvino", "Revoada Pálida", "Plumas do Corvo Pálido", "Armadura", {"agilidade": 14, "destreza": 7}),
]
