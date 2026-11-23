"""Gera avaliacao/perguntas.json: 20 perguntas com categoria, banco, ferramenta esperada, consulta de
referência e GABARITO calculado diretamente dos bancos (nunca do modelo).

Os nomes de negócios/usuários são escolhidos nos próprios dados (nomes únicos, ASCII), então o conjunto
é reprodutível em qualquer recorte. Rode uma vez, revise o JSON e congele-o (o Trabalho 3 reutiliza).

  python -m avaliacao.gerar_perguntas
Composição (20): SQL 8 (2 filtro, 2 item, 2 comparação, 2 cálculo) | Cypher 6 (2,1,1,2) | híbridas 3 (1 comparação,
2 cálculo) | especiais 3 (capciosa, item inexistente, fora do escopo).
"""
import json
import re
import sys

import psycopg2
from neo4j import GraphDatabase

PG = dict(dbname="yelp", user="postgres", password="postgres", host="localhost")
NEO = ("bolt://localhost:7687", ("neo4j", "senha12345"))
SAIDA = "avaliacao/perguntas.json"

con = psycopg2.connect(**PG)
con.set_session(readonly=True, autocommit=True)
cur = con.cursor()
drv = GraphDatabase.driver(*NEO)


def sql(q, p=None):
    cur.execute(q, p)
    return cur.fetchall()


def cy(q, **p):
    with drv.session() as s:
        return [r.data() for r in s.run(q, **p)]


def ascii_ok(s):
    return bool(s) and re.fullmatch(r"[ -~]+", s) is not None and "'" not in s


def usuario_unico(nome):
    return sql("SELECT COUNT(*) FROM users WHERE name = %s", (nome,))[0][0] == 1


def grau(uid):
    return cy("MATCH (u:User {user_id: $id})-[:FRIEND]-(f) RETURN count(f) AS n", id=uid)[0]["n"]


def falha(msg):
    sys.exit(f"[gerar_perguntas] {msg}")


def categoria_existe(c):
    return sql("SELECT COUNT(*) FROM business_category WHERE category = %s", (c,))[0][0] > 0


for c in ("Mexican", "Coffee & Tea", "Italian", "Pizza", "Restaurants"):
    if not categoria_existe(c):
        falha(f"categoria '{c}' não existe no recorte; ajuste o script.")

perguntas = []


def add(**kw):
    kw.setdefault("comportamento", "normal")
    kw.setdefault("modo_trajetoria", "qualquer")
    kw["id"] = f"Q{len(perguntas) + 1:02d}"
    kw["ferramenta_esperada"] = ("+" if kw["modo_trajetoria"] == "todas" else "|").join(kw["ferramentas_esperadas"]) or "(nenhuma)"
    perguntas.append(kw)


# ===================================================================== SQL (8)
# --- filtro 1
q = ("SELECT COUNT(DISTINCT b.business_id) FROM business b JOIN business_category c USING (business_id) "
     "WHERE c.category = 'Mexican' AND b.is_open = 1 AND b.stars >= 4")
n = sql(q)[0][0]
add(categoria="filtro", banco="sql", ferramentas_esperadas=["run_sql"],
    pergunta="Quantos restaurantes de comida mexicana estão abertos e têm 4 estrelas ou mais?",
    consulta_ref=q, gabarito=n, numericos=[n])

# --- filtro 2 (top 5 por CEP, sem empate na fronteira)
ceps = sql("SELECT postal_code FROM business WHERE is_open = 1 AND postal_code IS NOT NULL "
           "GROUP BY postal_code ORDER BY COUNT(*) DESC LIMIT 6")
for (cep,) in ceps:
    q = ("SELECT name, review_count FROM business WHERE postal_code = %s AND is_open = 1 "
         "ORDER BY review_count DESC, business_id LIMIT 6")
    top = sql(q, (cep,))
    if len(top) == 6 and top[4][1] != top[5][1] and all(ascii_ok(t[0]) for t in top[:5]):
        add(categoria="filtro", banco="sql", ferramentas_esperadas=["run_sql"],
            pergunta=f"Quais são os 5 negócios abertos com mais reviews (review_count) no CEP {cep}?",
            consulta_ref=q.replace("%s", f"'{cep}'"), gabarito=[t[0] for t in top[:5]],
            obrigatorios=[t[0] for t in top[:5]])
        break
else:
    falha("não achei CEP sem empate para a pergunta de filtro 2.")

# --- itens específicos (nomes únicos)
negs = sql("""WITH u AS (SELECT name FROM business GROUP BY name HAVING COUNT(*) = 1)
              SELECT business_id, name, stars, review_count, address, price_range
              FROM business JOIN u USING (name) ORDER BY review_count DESC LIMIT 80""")
negs = [x for x in negs if ascii_ok(x[1])]
b1 = negs[0]
add(categoria="item", banco="sql", ferramentas_esperadas=["business_details", "run_sql"],
    pergunta=f"Quantas estrelas e quantas reviews tem o negócio '{b1[1]}'?",
    consulta_ref=f"SELECT stars, review_count FROM business WHERE business_id = '{b1[0]}'",
    gabarito={"stars": float(b1[2]), "review_count": b1[3]}, numericos=[float(b1[2]), b1[3]],
    nota_juiz="Aceite review_count do Yelp (business.review_count).")
b2 = next(x for x in negs[1:] if x[5] is not None and x[4])
add(categoria="item", banco="sql", ferramentas_esperadas=["business_details", "run_sql"],
    pergunta=f"Qual é o endereço e a faixa de preço (price_range) do negócio '{b2[1]}'?",
    consulta_ref=f"SELECT address, price_range FROM business WHERE business_id = '{b2[0]}'",
    gabarito={"address": b2[4], "price_range": b2[5]}, obrigatorios=[b2[4]], numericos=[b2[5]])

# --- comparação 1 (compare_businesses)
par = None
for i in range(len(negs)):
    for j in range(i + 1, len(negs)):
        if negs[i][2] != negs[j][2]:
            par = (negs[i], negs[j])
            break
    if par:
        break
a, b = par
melhor = a if a[2] > b[2] else b
add(categoria="comparacao", banco="sql", ferramentas_esperadas=["compare_businesses", "run_sql"],
    pergunta=f"Entre '{a[1]}' e '{b[1]}', qual tem a maior nota média (stars) no Yelp e de quanto?",
    consulta_ref=f"SELECT name, stars FROM business WHERE business_id IN ('{a[0]}', '{b[0]}')",
    gabarito={"melhor": melhor[1], "stars": float(melhor[2])}, obrigatorios=[melhor[1]], numericos=[float(melhor[2])])

# --- comparação 2 (categorias)
q = ("SELECT category, COUNT(DISTINCT business_id) AS n FROM business_category "
     "WHERE category IN ('Mexican', 'Italian', 'Pizza') GROUP BY category ORDER BY n DESC")
rk = sql(q)
add(categoria="comparacao", banco="sql", ferramentas_esperadas=["run_sql"],
    pergunta="Qual tem mais negócios no Yelp de Santa Barbara: a categoria mexicana, a italiana ou a de pizza? Diga quantos.",
    consulta_ref=q, gabarito={c: n for c, n in rk}, obrigatorios_qualquer=[rk[0][0]], numericos=[rk[0][1]])

# --- cálculo 1
q = ("SELECT ROUND(AVG(b.stars)::numeric, 3) FROM business b JOIN business_category c USING (business_id) "
     "WHERE c.category = 'Coffee & Tea' AND b.is_open = 1")
v = float(sql(q)[0][0])
add(categoria="calculo", banco="sql", ferramentas_esperadas=["run_sql"],
    pergunta="Qual é a média de estrelas (coluna stars) dos negócios abertos da categoria de cafés e chás (Coffee & Tea)?",
    consulta_ref=q, gabarito=v, numericos=[v], tol=0.06)

# --- cálculo 2
q = "SELECT COUNT(*), ROUND(AVG(stars)::numeric, 3) FROM review WHERE EXTRACT(YEAR FROM date) = 2019"
c19, m19 = sql(q)[0]
add(categoria="calculo", banco="sql", ferramentas_esperadas=["run_sql"],
    pergunta="Quantas reviews foram escritas em 2019 e qual foi a média de estrelas delas?",
    consulta_ref=q, gabarito={"reviews": c19, "media": float(m19)}, numericos=[c19, float(m19)], tol=0.06)

# ================================================================== CYPHER (6)
top = cy("MATCH (u:User)-[:FRIEND]-() RETURN u.user_id AS id, u.name AS nome, count(*) AS g ORDER BY g DESC LIMIT 400")
cand_u = [t for t in top if ascii_ok(t["nome"]) and t["g"] >= 15 and usuario_unico(t["nome"])]
sel = None
for u in cand_u[:30]:
    amigos = cy("MATCH (:User {user_id: $id})-[:FRIEND]-(f:User) RETURN f.user_id AS id, f.name AS nome", id=u["id"])
    for f in amigos[:80]:
        if not (ascii_ok(f["nome"]) and usuario_unico(f["nome"])):
            continue
        comuns = cy("MATCH (:User {user_id: $a})-[:REVIEWED]->(b:Business)<-[:REVIEWED]-(:User {user_id: $b}) RETURN b.name AS nome",
                    a=u["id"], b=f["id"])
        gf = grau(f["id"])
        if 1 <= len(comuns) <= 8 and gf != u["g"]:
            sel = (u, f, [c["nome"] for c in comuns], gf)
            break
    if sel:
        break
if not sel:
    falha("não achei par de amigos com negócios em comum para as perguntas de grafo.")
u, f, comuns, gf = sel
add(categoria="filtro", banco="cypher", ferramentas_esperadas=["run_cypher"],
    pergunta=f"Quantos amigos diretos tem o usuário '{u['nome']}'?",
    consulta_ref=f"MATCH (u:User {{user_id: '{u['id']}'}})-[:FRIEND]-(f) RETURN count(f)", gabarito=u["g"], numericos=[u["g"]])
add(categoria="filtro", banco="cypher", ferramentas_esperadas=["run_cypher"],
    pergunta=f"Quais negócios foram avaliados tanto por '{u['nome']}' quanto por '{f['nome']}'?",
    consulta_ref=(f"MATCH (:User {{user_id: '{u['id']}'}})-[:REVIEWED]->(b)<-[:REVIEWED]-(:User {{user_id: '{f['id']}'}}) RETURN b.name"),
    gabarito=comuns, obrigatorios=comuns)
add(categoria="item", banco="cypher", ferramentas_esperadas=["run_cypher"],
    pergunta=f"O usuário '{u['nome']}' é amigo direto do usuário '{f['nome']}'?",
    consulta_ref=f"MATCH (:User {{user_id: '{u['id']}'}})-[r:FRIEND]-(:User {{user_id: '{f['id']}'}}) RETURN count(r) > 0",
    gabarito=True, obrigatorios_qualquer=["sim", "são amigos", "é amigo", "eh amigo"], proibidos=["não são amigos", "nao sao amigos"])
venc = u if u["g"] > gf else f
add(categoria="comparacao", banco="cypher", ferramentas_esperadas=["run_cypher"],
    pergunta=f"Quem tem mais amigos diretos, '{u['nome']}' ou '{f['nome']}'? Quantos amigos cada um tem?",
    consulta_ref="grau de cada usuário via MATCH (u)-[:FRIEND]-(f) RETURN count(f)",
    gabarito={u["nome"]: u["g"], f["nome"]: gf, "vencedor": venc["nome"]},
    obrigatorios=[venc["nome"]], numericos=[u["g"], gf])

# usuário de grau médio para dois saltos, caminho e perguntas híbridas
cand = cy("MATCH (u:User)-[:FRIEND]-() WITH u, count(*) AS g WHERE g >= 8 AND g <= 40 "
          "RETURN u.user_id AS id, u.name AS nome, g LIMIT 400")
sel2 = None
for uc in cand:
    if uc["id"] == u["id"] or not (ascii_ok(uc["nome"]) and usuario_unico(uc["nome"])):
        continue
    fof = cy("MATCH (a:User {user_id: $id})-[:FRIEND]-(:User)-[:FRIEND]-(g:User) "
             "WHERE g <> a AND NOT (a)-[:FRIEND]-(g) RETURN DISTINCT g.user_id AS id, g.name AS nome", id=uc["id"])
    alvo = next((x for x in fof if ascii_ok(x["nome"]) and usuario_unico(x["nome"])), None)
    if not alvo:
        continue
    amigos = [x["id"] for x in cy("MATCH (:User {user_id: $id})-[:FRIEND]-(f:User) RETURN f.user_id AS id", id=uc["id"])]
    topo = sql("SELECT r.user_id, u.name, COUNT(*) AS n FROM review r JOIN users u USING (user_id) "
               "WHERE r.user_id = ANY(%s) GROUP BY r.user_id, u.name ORDER BY n DESC LIMIT 2", (amigos,))
    media, nrev = sql("SELECT AVG(stars), COUNT(*) FROM review WHERE user_id = ANY(%s)", (amigos,))[0]
    nneg = sql("SELECT COUNT(DISTINCT b.business_id) FROM review r JOIN business b USING (business_id) "
               "JOIN business_category c USING (business_id) WHERE r.user_id = ANY(%s) AND b.is_open = 1 "
               "AND c.category = 'Restaurants'", (amigos,))[0][0]
    if len(topo) == 2 and topo[0][2] > topo[1][2] and nrev >= 10 and nneg > 0 and ascii_ok(topo[0][1]):
        sel2 = (uc, len(fof), alvo, amigos, topo, float(media), nrev, nneg)
        break
if not sel2:
    falha("não achei usuário adequado para as perguntas de dois saltos/híbridas.")
uc, n2, alvo, amigos, topo, media, nrev, nneg = sel2
add(categoria="calculo", banco="cypher", ferramentas_esperadas=["run_cypher"],
    pergunta=(f"Quantos 'amigos de amigos' tem o usuário '{uc['nome']}' (pessoas a exatamente 2 saltos de amizade, "
              "que não são seus amigos diretos nem ele mesmo)?"),
    consulta_ref=(f"MATCH (a:User {{user_id: '{uc['id']}'}})-[:FRIEND]-(:User)-[:FRIEND]-(g:User) "
                  "WHERE g <> a AND NOT (a)-[:FRIEND]-(g) RETURN count(DISTINCT g)"),
    gabarito=n2, numericos=[n2])
saltos = cy("MATCH p = shortestPath((a:User {user_id: $a})-[:FRIEND*..6]-(b:User {user_id: $b})) RETURN length(p) AS n",
            a=uc["id"], b=alvo["id"])[0]["n"]
add(categoria="calculo", banco="cypher", ferramentas_esperadas=["run_cypher"],
    pergunta=f"Qual é o menor número de saltos de amizade entre '{uc['nome']}' e '{alvo['nome']}'?",
    consulta_ref=(f"MATCH p = shortestPath((a:User {{user_id: '{uc['id']}'}})-[:FRIEND*..6]-(b:User {{user_id: '{alvo['id']}'}})) RETURN length(p)"),
    gabarito=saltos, numericos=[saltos])

# ================================================================= HÍBRIDAS (3)
add(categoria="comparacao", banco="ambos", ferramentas_esperadas=["run_cypher", "run_sql"], modo_trajetoria="todas",
    pergunta=f"Entre os amigos diretos de '{uc['nome']}', quem escreveu mais reviews em Santa Barbara (tabela review)? Quantas?",
    consulta_ref="amigos via Cypher; depois SELECT user_id, COUNT(*) FROM review WHERE user_id = ANY(ids) GROUP BY 1 ORDER BY 2 DESC LIMIT 1",
    gabarito={"usuario": topo[0][1], "reviews": topo[0][2]}, obrigatorios=[topo[0][1]], numericos=[topo[0][2]])
add(categoria="calculo", banco="ambos", ferramentas_esperadas=["run_cypher", "run_sql"], modo_trajetoria="todas",
    pergunta=f"Qual é a média de estrelas de todas as reviews escritas em Santa Barbara pelos amigos diretos de '{uc['nome']}'?",
    consulta_ref="amigos via Cypher; depois SELECT AVG(stars) FROM review WHERE user_id = ANY(ids)",
    gabarito=media, numericos=[media], tol=0.06)
add(categoria="calculo", banco="ambos", ferramentas_esperadas=["run_cypher", "run_sql"], modo_trajetoria="todas",
    pergunta=(f"Quantos negócios abertos da categoria Restaurants foram avaliados por amigos diretos de '{uc['nome']}'? "
              "Conte cada negócio uma vez."),
    consulta_ref="amigos via Cypher; depois COUNT(DISTINCT business_id) em review JOIN business JOIN business_category (Restaurants, is_open=1)",
    gabarito=nneg, numericos=[nneg])

# =================================================================== ESPECIAIS (3)
cap = sql("""SELECT u.user_id, u.name, u.review_count, COUNT(r.review_id) AS loc
             FROM users u JOIN review r USING (user_id)
             WHERE u.review_count BETWEEN 50 AND 2000
             GROUP BY u.user_id, u.name, u.review_count
             HAVING COUNT(r.review_id) >= 3 AND COUNT(r.review_id) < u.review_count * 0.5
             ORDER BY u.review_count DESC LIMIT 200""")
cap = next((x for x in cap if ascii_ok(x[1]) and usuario_unico(x[1])), None)
if not cap:
    falha("não achei usuário para a pergunta capciosa.")
add(categoria="capciosa", comportamento="capciosa", banco="sql", ferramentas_esperadas=["run_sql"],
    pergunta=f"Quantas reviews o usuário '{cap[1]}' escreveu?",
    consulta_ref=f"users.review_count = {cap[2]} (Yelp inteiro) x SELECT COUNT(*) FROM review WHERE user_id = '{cap[0]}' = {cap[3]} (Santa Barbara)",
    gabarito={"yelp_inteiro": cap[2], "santa_barbara": cap[3]}, numericos_qualquer=[cap[2], cap[3]],
    nota_juiz=(f"PEGADINHA: users.review_count ({cap[2]}) conta o Yelp inteiro; contar linhas em review dá só Santa Barbara ({cap[3]}). "
               "Correto = deixar claro qual contagem está dando (ou dar as duas). Incorreto = dar um dos números como se fosse o único e absoluto sem explicar o escopo."))
nome_ficticio = "Cantina do Pantano Voador"
assert sql("SELECT COUNT(*) FROM business WHERE name ILIKE %s", (f"%{nome_ficticio}%",))[0][0] == 0
add(categoria="inexistente", comportamento="inexistente", banco="sql", ferramentas_esperadas=["find_entity", "run_sql"],
    pergunta=f"Qual é a nota média do restaurante '{nome_ficticio}'?",
    consulta_ref=f"SELECT * FROM business WHERE name ILIKE '%{nome_ficticio}%' -> 0 linhas", gabarito="não existe no recorte",
    obrigatorios_qualquer=["não encontr", "não existe", "inexist", "não há", "nenhum", "não localiz"],
    nota_juiz="Correto = consultar, receber vazio e dizer que o negócio não existe no recorte, SEM inventar nota.")
add(categoria="fora_do_escopo", comportamento="fora_do_escopo", banco="nenhum", ferramentas_esperadas=[],
    pergunta="Qual vai ser a previsão do tempo em Santa Barbara amanhã?",
    consulta_ref="(nenhuma)", gabarito="recusar sem consultar",
    obrigatorios_qualquer=["fora do", "não consigo", "não posso", "não tenho", "escopo", "apenas sobre", "só consigo"],
    nota_juiz="Correto = recusar educadamente, sem chamar ferramenta e sem inventar previsão.")

assert len(perguntas) == 20, len(perguntas)
with open(SAIDA, "w", encoding="utf-8") as fh:
    json.dump(perguntas, fh, ensure_ascii=False, indent=2, default=str)
print(f"{len(perguntas)} perguntas gravadas em {SAIDA}")
for p in perguntas:
    print(f"{p['id']} [{p['categoria']:<14}|{p['banco']:<6}] {p['pergunta'][:90]}")
