"""Esquemas em texto. Servem de resource MCP (schema://sql, schema://graph)
e são injetados nos prompts dos agentes (a aplicação lê o resource)."""

ESQUEMA_SQL = """\
BANCO RELACIONAL (PostgreSQL) - Yelp, somente Santa Barbara, CA. Ferramenta: run_sql.
Nomes de tabelas/colunas e valores de texto estão em INGLÊS (ex.: categoria 'Mexican', 'Coffee & Tea').

business(business_id, name, address, postal_code, latitude, longitude, stars, review_count, is_open, price_range, checkin_count)
  stars: nota média 1-5 (passos de 0.5) | is_open: 1 aberto, 0 fechado
  price_range: 1 a 4, NULL em mais da metade dos negócios | 142 nomes de negócio se repetem (redes)
business_category(business_id, category)
  vários registros por negócio; para filtrar por categoria faça JOIN com business
review(review_id, user_id, business_id, stars, date, useful, funny, cool)
  uma linha por avaliação feita em Santa Barbara (um usuário pode avaliar o mesmo negócio mais de uma vez)
users(user_id, name, review_count, yelping_since, useful, funny, cool, fans, average_stars)
  a tabela se chama users (user é palavra reservada)

ATENÇÃO - contadores globais x recorte:
  users.review_count e users.average_stars contam o Yelp INTEIRO, não só Santa Barbara.
  Para contar/ponderar só Santa Barbara use COUNT(*)/AVG(stars) na tabela review.
  business.review_count é o valor do Yelp (muito próximo, mas não idêntico, ao número de linhas em review).
"""

ESQUEMA_GRAFO = """\
BANCO DE GRAFOS (Neo4j, Cypher 5) - mesmos usuários e negócios de Santa Barbara. Ferramenta: run_cypher.
Nós:
  (:User {user_id, name})
  (:Business {business_id, name})
Relações:
  (:User)-[:FRIEND]-(:User)        amizade, guardada em uma direção: consulte SEM direção: (a)-[:FRIEND]-(b)
  (:User)-[:REVIEWED]->(:Business) uma por par usuário-negócio
O grafo NÃO tem notas, categorias, datas nem contagens de reviews (isso fica no SQL).
Só leitura: MATCH/WHERE/RETURN/WITH/ORDER BY/LIMIT. Rótulos e propriedades diferenciam maiúsculas de minúsculas.
Exemplos:
  MATCH (u:User {name: 'Fulano'})-[:FRIEND]-(f:User) RETURN count(f) AS amigos
  MATCH p = shortestPath((a:User {user_id: 'ID1'})-[:FRIEND*..6]-(b:User {user_id: 'ID2'})) RETURN length(p) AS saltos
Dica: use find_entity(name, 'usuario') para obter o user_id a partir do nome. Nomes de usuário podem se repetir.
"""
