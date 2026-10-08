#!/bin/bash
# Startet ein Skript aus skripte/ in einem Client-Container im Docker-Netz der
# Datenbanken. Die Verbindung laeuft ueber die Dienstnamen (postgres, mongodb)
# statt ueber localhost, also ohne die Portweiterleitung von Docker Desktop.
# Diese fiel bei anhaltendem Verkehr zu MongoDB aus (siehe ergebnisse/stand.md).
#
# Aufruf: Skriptname, danach dessen Parameter, z. B.:
#   skripte/client_container.sh bench.py --arm B --profil L4 --abfrage Q2 --sf sf1 --lauf 99 --clients 4
#   skripte/client_container.sh load_data.py --daten daten/sf1 --drop
#   skripte/client_container.sh mongo_setup.py
set -e
cd "$(dirname "$0")/.."

SKRIPT="$1"
if [ -z "$SKRIPT" ] || [ ! -f "skripte/$SKRIPT" ]; then
    echo "Aufruf: $0 <skript in skripte/> [Parameter ...]" >&2
    exit 2
fi
shift

NETZ="bachelor-benchmark_default"     # von docker compose angelegt (Projektname + _default)
IMAGE="benchmark-client"
NAME="arm_client"                     # fester Name, damit docker stats ihn findet

# Feste Limits fuer den Lastgenerator: Er laeuft in derselben VM wie die
# Datenbanken. Ohne Limit koennte er der gemessenen Datenbank Rechenzeit
# wegnehmen, und zwar je nach Arm unterschiedlich viel.
# 4 CPUs: bench.py startet je Client einen Prozess; vier Prozesse brauchen bis
#   zu vier Kerne. Gemessene Datenbank (4 CPUs) plus Client (4 CPUs) muessen in
#   die CPUs der Docker-VM passen (8).
# 1,5 GB: Die Prozesse halten alle Antwortzeiten bis zum Ende des Laufs im
#   Speicher, und Q2 liefert auf SF2 groessere Ergebnismengen als auf SF1.
CPUS="4"
SPEICHER="1536m"

# Image nur bauen, wenn es noch nicht existiert. docker build fragt sonst vor
# jedem Lauf Docker Hub nach dem Basis-Image und scheitert ohne Netz.
# Nach einer Aenderung am Dockerfile: docker rmi benchmark-client
if ! docker image inspect "$IMAGE" > /dev/null 2>&1; then
    docker build -q -t "$IMAGE" docker/client > /dev/null
fi

# Rest eines abgebrochenen Laufs entfernen, sonst ist der Name belegt
docker rm -f "$NAME" > /dev/null 2>&1 || true

# Das Projektverzeichnis wird eingebunden, damit die Skripte dieselben Daten
# lesen und in dieselbe ergebnisse/messungen.csv schreiben wie beim Start vom Host.
# TZ: Zeitstempel in der Ergebnisdatei in Ortszeit statt UTC.
# PYTHONUNBUFFERED: Ausgaben erscheinen sofort, nicht erst am Ende.
# --init: Signale (Strg+C) kommen sauber beim Skript an.
exec docker run --rm --init --name "$NAME" --network "$NETZ" \
    --cpus "$CPUS" --memory "$SPEICHER" \
    -v "$PWD":/projekt -w /projekt \
    -e TZ=Europe/Berlin -e PYTHONUNBUFFERED=1 \
    -e PG_HOST=postgres -e MONGO_HOST=mongodb \
    "$IMAGE" python "skripte/$SKRIPT" "$@"
