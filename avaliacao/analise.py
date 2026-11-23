"""Análise: tabela comparativa, gráfico acerto x custo, erros por pergunta e concordância com o juiz.
Gera em resultados/: tabela_comparativa.csv, grafico_acerto_custo.png, erros_por_pergunta.csv, concordancia.txt,
e o modelo resultados/revisao_manual.csv (preencha a coluna veredito_manual com correto/incorreto e rode de novo).

  python -m avaliacao.analise
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

R = "resultados"
df = pd.read_csv(f"{R}/resultados.csv")
df["juiz_ok"] = df["juiz_veredito"].eq("correto") if "juiz_veredito" in df else np.nan
df["tokens_total"] = df["tokens_in"] + df["tokens_out"]

# ---- tabela comparativa ----
g = df.groupby("sistema")
tab = pd.DataFrame({
    "n": g.size(),
    "acerto_juiz": g["juiz_ok"].mean(),
    "acerto_auto": g["auto_ok"].mean(),
    "trajetoria_ok": g["trajetoria_ok"].mean(),
    "lat_media_s": g["segundos"].mean(),
    "lat_mediana_s": g["segundos"].median(),
    "lat_p95_s": g["segundos"].quantile(0.95),
    "tokens_in_medio": g["tokens_in"].mean(),
    "tokens_out_medio": g["tokens_out"].mean(),
    "chamadas_modelo_medio": g["chamadas_modelo"].mean(),
    "chamadas_ferramenta_medio": g["chamadas_ferramenta"].mean(),
    "cpu_medio_pct": g["cpu_medio"].mean(),
    "ram_mb_max": g["ram_mb_max"].max(),
    "gpu_util_medio_pct": g["gpu_util_medio"].mean(),
    "gpu_mem_mb_max": g["gpu_mem_mb_max"].max(),
    "erros_execucao": g["erro"].apply(lambda s: s.notna().sum()),
}).round(3)
tab.to_csv(f"{R}/tabela_comparativa.csv")
print(tab.T.to_string(), "\n")

# ---- por categoria ----
print("Acerto (juiz) por categoria x sistema:")
print(df.pivot_table(index="categoria", columns="sistema", values="juiz_ok", aggfunc="mean").round(2).to_string(), "\n")

# ---- gráfico acerto x custo ----
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
tok = g["tokens_total"].mean()
for i, (xs, rot) in enumerate([(tab["lat_media_s"], "latência média (s)"), (tok, "tokens por pergunta (entrada + saída)")]):
    for s in tab.index:
        ax[i].scatter(xs[s], tab.loc[s, "acerto_juiz"] * 100, s=140)
        ax[i].annotate(s, (xs[s], tab.loc[s, "acerto_juiz"] * 100), textcoords="offset points", xytext=(8, 6))
    ax[i].set_xlabel(rot)
    ax[i].set_ylabel("acerto segundo o juiz (%)")
    ax[i].grid(alpha=.3)
fig.suptitle("Acerto x custo por arquitetura")
fig.tight_layout()
fig.savefig(f"{R}/grafico_acerto_custo.png", dpi=150)

# ---- erros por pergunta ----
p = df.pivot_table(index=["id", "categoria", "banco"], columns="sistema", values="juiz_veredito", aggfunc="first")
p["erraram"] = (p == "incorreto").sum(axis=1)
p.to_csv(f"{R}/erros_por_pergunta.csv")
print("Perguntas com mais erros:\n", p.sort_values("erraram", ascending=False).head(8).to_string(), "\n")


# ---- concordância ----
def kappa(a, b):
    a, b = np.asarray(a), np.asarray(b)
    po = (a == b).mean()
    pe = sum((a == c).mean() * (b == c).mean() for c in set(a) | set(b))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


modelo = f"{R}/revisao_manual.csv"
if not os.path.exists(modelo):
    df.reindex(columns=["id", "sistema", "pergunta", "resposta", "juiz_veredito", "juiz_justificativa"]).assign(
        veredito_manual="", comentario="").to_csv(modelo, index=False)
    print(f"Modelo de revisão criado em {modelo}: preencha 'veredito_manual' (correto/incorreto) e rode de novo.")
else:
    man = pd.read_csv(modelo)[["id", "sistema", "veredito_manual"]].dropna()
    m = df.merge(man, on=["id", "sistema"])
    if len(m):
        m["manual_ok"] = m["veredito_manual"].str.strip().str.lower().eq("correto")
        linhas = [f"revisões manuais: {len(m)}"]
        for nome, col in [("humano x juiz", "juiz_ok"), ("humano x checagem automática", "auto_ok")]:
            conc = (m["manual_ok"] == m[col]).mean()
            linhas.append(f"{nome}: concordância {conc:.1%}, kappa de Cohen {kappa(m['manual_ok'], m[col]):.2f}")
        disc = m[m["manual_ok"] != m["juiz_ok"]][["id", "sistema", "juiz_veredito", "veredito_manual"]]
        linhas.append(f"discordâncias humano x juiz ({len(disc)}):\n{disc.to_string(index=False)}")
        with open(f"{R}/concordancia.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(linhas))
        print("\n".join(linhas))
