"""Registra hardware, modelo, quantização e versões das bibliotecas em resultados/ambiente.json."""
import json
import os
import platform
import subprocess
from importlib import metadata

PACOTES = ["langgraph", "langchain-core", "langchain-ollama", "langchain-mcp-adapters", "fastmcp", "mcp",
           "psycopg2-binary", "neo4j", "google-genai", "pandas", "psutil"]


def _cmd(c):
    try:
        return subprocess.run(c, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return None


def coletar(modelo="qwen3:8b") -> dict:
    versoes = {}
    for p in PACOTES:
        try:
            versoes[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            versoes[p] = None
    import psutil
    return {
        "python": platform.python_version(),
        "so": platform.platform(),
        "cpu": platform.processor() or _cmd(["bash", "-c", "grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2"]),
        "cpu_nucleos": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "gpu": _cmd(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]),
        "ollama": _cmd(["ollama", "--version"]),
        "modelo": modelo,
        "modelo_detalhes": _cmd(["ollama", "show", modelo]),  # contém a quantização (ex.: Q4_K_M)
        "bibliotecas": versoes,
        "servidor_libs": _cmd(["/content/venv_servidor/bin/pip", "list", "--format=freeze"]) if os.path.exists("/content/venv_servidor") else None,
    }


if __name__ == "__main__":
    os.makedirs("resultados", exist_ok=True)
    with open("resultados/ambiente.json", "w", encoding="utf-8") as f:
        json.dump(coletar(), f, ensure_ascii=False, indent=2)
    print("resultados/ambiente.json gravado")
