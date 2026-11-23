#!/usr/bin/env bash
# Sobe o servidor MCP (HTTP, porta 8765) no ambiente separado. Rode da raiz do repositório.
pkill -f "servidor/server.py" 2>/dev/null || true
nohup /content/venv_servidor/bin/python servidor/server.py --http --host 127.0.0.1 --port 8765 > /content/servidor.log 2>&1 &
sleep 6
tail -n 8 /content/servidor.log
