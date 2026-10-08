# Client-Container fuer den Lastgenerator im selben Docker-Netz wie die Datenbanken.
# Dient der Gegenprobe ohne die Portweiterleitung von Docker Desktop.
# Python-Version und Paketversionen entsprechen der virtuellen Umgebung .venv
# (siehe ergebnisse/umgebung.txt).
FROM python:3.9.6-slim
RUN pip install --no-cache-dir "psycopg[binary]==3.2.13" "pymongo==4.18.2"
WORKDIR /projekt
