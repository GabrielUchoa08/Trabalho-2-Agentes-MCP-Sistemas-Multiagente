#!/usr/bin/env bash
# Prepara a sessão do Colab (T4, sem Docker): PostgreSQL, Neo4j 5.26, Ollama + qwen3:8b e os ambientes Python.
# Rode a partir da raiz do repositório:  bash colab/01_preparar.sh
set -e
REPO=$(pwd)
NEO=/content/neo4j-community-5.26.0

echo "== apt =="
apt-get -qq update
DEBIAN_FRONTEND=noninteractive apt-get -qq install -y postgresql openjdk-17-jre-headless zstd pciutils lshw curl wget > /dev/null

echo "== PostgreSQL =="
service postgresql start
sleep 3
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='yelp'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE yelp;"

echo "== Neo4j =="
if [ ! -d "$NEO" ]; then
  wget -q https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz -O /content/neo4j.tgz
  tar -xzf /content/neo4j.tgz -C /content
fi
$NEO/bin/neo4j-admin dbms set-initial-password senha12345 || true
grep -q "heap.max_size" $NEO/conf/neo4j.conf || cat >> $NEO/conf/neo4j.conf <<CONF
server.memory.heap.initial_size=1G
server.memory.heap.max_size=2G
server.memory.pagecache.size=1G
CONF
$NEO/bin/neo4j start || true

echo "== Ollama =="
command -v ollama > /dev/null || curl -fsSL https://ollama.com/install.sh | sh
pgrep -x ollama > /dev/null || (nohup ollama serve > /content/ollama.log 2>&1 &)
sleep 6
ollama pull qwen3:8b

echo "== Python =="
python3 -m venv /content/venv_servidor
/content/venv_servidor/bin/pip -q install -r "$REPO/servidor/requirements.txt"
pip -q install -r "$REPO/agentes/requirements.txt"
echo "preparação concluída"
