# Trabalho 2 - Agentes, MCP e Sistemas Multiagente (DCA0305)

Consulta em linguagem natural ao **Yelp (Santa Barbara, CA)** guardado em dois bancos: **PostgreSQL** (atributos,
reviews, usuários) e **Neo4j** (amizades e quem avaliou quem). Um servidor **MCP** expõe os bancos; três sistemas
(agente único, roteamento, gerador com verificador) usam o **mesmo modelo** (`qwen3:8b` no Ollama) e são comparados
em 20 perguntas (60 execuções), com juiz de fronteira (Gemini) e verificação manual.

> **Vídeo (15-20 min):** _colar o link aqui_ · **Integrantes:** _preencher_

## Arquitetura

```
 Agentes (LangGraph + qwen3:8b/Ollama)             Servidor MCP (FastMCP, HTTP :8765)         Bancos
 ┌──────────────────────────────────────┐          ┌──────────────────────────────────┐   ┌─────────────┐
 │ 1 unico        ReAct único           │          │ tools: run_sql  run_cypher       │──▶│ PostgreSQL  │ (usuário "leitor",
 │ 2 roteamento   roteador → especialista│──MCP───▶│        find_entity               │   │ yelp        │  só SELECT, timeout)
 │                (sql | cypher | ambos)│          │        business_details          │   ├─────────────┤
 │ 3 verificador  gerador → verificador │          │        compare_businesses (cálculo)│──▶│ Neo4j 5     │ (tx de leitura,
 └──────────────────────────────────────┘          │ resources: schema://sql, ://graph│   │ User/Business│  bloqueio de escrita)
        answer(pergunta) → resposta + métricas      │ prompt: analisar_negocio         │   └─────────────┘
```

| Sistema | Ideia | Hipótese testada |
|---|---|---|
| `unico` | um agente com os dois esquemas e as 5 ferramentas | linha de base |
| `roteamento` | roteador (saída estruturada) escolhe `sql`/`cypher`/`ambos`/`fora_do_escopo`; cada especialista vê só o seu esquema; `ambos` = grafo → SQL → redator | menos contexto por agente melhora a consulta |
| `verificador` | o agente único gera; um verificador confere consulta × resposta × resultados (objeção só com evidência, máx. 2 devoluções; não olha sintaxe nem contesta resultado vazio) | segunda leitura pega erros de filtro/aritmética |

Decisões de projeto que valem citar no vídeo:
- Dois ambientes Python **separados** (`/content/venv_servidor` com FastMCP 4 e o ambiente dos agentes com `mcp` 1.x dos adaptadores do LangChain): instalados juntos eles se quebram. Os agentes falam com o servidor por **HTTP** (`/mcp`).
- Segurança dos bancos fica **no banco**, não só na ferramenta (usuário `leitor`, transação read-only, `statement_timeout`; no Neo4j, `execute_read` + bloqueio de `CREATE/MERGE/DELETE/SET/CALL`...).
- `SELECT * FROM user` no PostgreSQL **não dá erro** (devolve o usuário da sessão). A ferramenta bloqueia e orienta usar `users`.
- Erros viram dicionário `{"ok": false, "erro", "dica"}` (com a lista de tabelas/colunas válidas) para o modelo se recuperar no loop.
- Os esquemas (resources) são lidos de `servidor/esquemas.py`, a mesma fonte do resource, e injetados nos prompts.

## Estrutura

```
servidor/    server.py (MCP) · esquemas.py · validar_servidor.py (validação SEM LLM) · requirements.txt
agentes/     comum.py (modelo, métricas, interface answer) · unico.py · roteamento.py · verificador.py
avaliacao/   gerar_perguntas.py (20 perguntas + gabarito) · avaliador.py (60 execuções) · juiz.py (Gemini)
             checagem.py (trajetória/valores) · analise.py (tabela, gráfico, erros, kappa) · ambiente.py
dados/       carregar_bancos.py (CSV → PostgreSQL e Neo4j)
colab/       01_preparar.sh · 02_servidor.sh
notebooks/   executar_tudo.ipynb  ← ponto de entrada
resultados/  validacao_mcp.json · resultados.csv · tabela_comparativa.csv · grafico_acerto_custo.png ·
             erros_por_pergunta.csv · revisao_manual.csv · concordancia.txt · ambiente.json
slides/      apresentação
```

## Como executar (Colab, GPU T4, sem Docker)

1. Coloque no Drive a pasta `yelp_recorte/` com `business.csv, business_category.csv, users.csv, review.csv, friends.csv, reviewed.csv` (os dados **não** vão para o GitHub).
2. Guarde a chave do Google AI Studio em *Secrets* do Colab como `GOOGLE_API_KEY`.
3. Abra `notebooks/executar_tudo.ipynb` e rode de cima para baixo (ajuste `REPO_URL`).
   Ou, por terminal, a partir da raiz: `bash colab/01_preparar.sh` → `python dados/carregar_bancos.py --dir <pasta>` →
   `bash colab/02_servidor.sh` → `/content/venv_servidor/bin/python servidor/validar_servidor.py http://127.0.0.1:8765/mcp` →
   `python -m avaliacao.gerar_perguntas` → `python -m avaliacao.avaliador` → `python -m avaliacao.juiz` → `python -m avaliacao.analise`.

Variáveis úteis: `MODELO` (padrão `qwen3:8b`), `NUM_CTX` (8192), `MCP_URL`, `JUIZ_MODELO` (`gemini-2.5-flash`), `PAUSA_S` (pausa entre chamadas do juiz).

## Avaliação

- **20 perguntas**: SQL 8 · Cypher 6 · híbridas 3 · especiais 3 (capciosa: contador global × recorte; item inexistente; fora do escopo). Cada uma tem categoria, banco, ferramenta esperada, consulta de referência e **gabarito calculado dos dados** (`gerar_perguntas.py`).
- **Métricas por execução** (`resultados.csv`): acerto (juiz), trajetória, latência (média/mediana/p95 na análise), tokens de entrada/saída somados entre todos os agentes, chamadas ao modelo e às ferramentas, CPU/RAM/GPU amostradas a 0,5 s, além de `ambiente.json` (hardware, versões, quantização).
- **Juiz** Gemini com acesso somente leitura ao MCP (`run_sql`/`run_cypher`); registra veredito, justificativa e as consultas que fez. **Verificação manual:** preencha `resultados/revisao_manual.csv` e rode `analise` para obter concordância e kappa (humano × juiz e humano × checagem automática).
- `checagem.py` é uma heurística (números/nomes esperados). Serve de apoio, **não** substitui o juiz.

## O que foi testado e o que ainda NÃO foi

Testado em sandbox (PostgreSQL 16 + FastMCP 4.2.4 + LangGraph 0.6, dados sintéticos): carga no PostgreSQL (NULLs, colunas extras), servidor MCP por stdio e HTTP, validação sem LLM (23/24 testes; o 24º exige Neo4j), os três grafos de ponta a ponta com ferramentas MCP reais e um modelo roteirizado, coleta de métricas, `checagem.py` e `analise.py` (inclusive kappa).

**Não foi possível testar aqui** (você descobre na primeira execução): Neo4j (instalação, carga, `run_cypher`), Ollama + `qwen3:8b` (parâmetro `reasoning=False`, saída estruturada do roteador/verificador), a API do Gemini no juiz, GPU/`nvidia-smi`, e o `gerar_perguntas.py` com os dados reais. Se algo falhar, veja `/content/servidor.log`, `/content/ollama.log` e `$NEO/logs/neo4j.log`.

## Pendências de entrega (29/11/2026, 23h59)

- [ ] Rodar tudo e revisar `perguntas.json` (confira os gabaritos à mão em 3-4 perguntas)
- [ ] Verificação manual dos vereditos do juiz (`revisao_manual.csv`)
- [ ] Texto de conclusão com a tabela e o gráfico (um agente basta, ou o multiagente se justifica?)
- [ ] Slides em `slides/` · vídeo de 15-20 min (todos com rosto visível, link acima)
- [ ] Dar acesso de leitura aos professores · submeter o link do repositório
