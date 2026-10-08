"""
Laedt die von generate_data.py erzeugten Dateien in alle drei Datenbanken.

Beispiele:
    python skripte/load_data.py --daten daten/sf1
    python skripte/load_data.py --daten daten/sf1 --arm A
    python skripte/load_data.py --daten daten/sf1 --arm B --drop
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Host und Port aus Umgebungsvariablen wie in bench.py; Standard ist localhost.
# Im Client-Container zeigen sie auf die Dienstnamen im Docker-Netz.
PG_HOST     = os.environ.get("PG_HOST", "localhost")
PG_PORT     = int(os.environ.get("PG_PORT", "5432"))
PG_USER     = "postgres"
PG_PASSWORD = "test"
PG_DBNAME   = "arm_a"

MONGO_URL        = (f"mongodb://{os.environ.get('MONGO_HOST', 'localhost')}"
                    f":{os.environ.get('MONGO_PORT', '27017')}")
BLOCK_GROESSE    = 10_000   # Dokumente je insert_many-Aufruf
FORTSCHRITT_ALLE = 100_000  # Fortschrittsmeldung alle N Zeilen

SCHEMA_PFAD  = Path(__file__).resolve().parent.parent / "sql" / "schema_postgres.sql"
INDIZES_PFAD = Path(__file__).resolve().parent.parent / "sql" / "indizes_postgres.sql"


# --- Hilfsfunktion: SQL-Datei ausfuehren --------------------------------

def sql_ausfuehren(cur, pfad, bezeichnung):
    """
    Liest eine SQL-Datei und fuehrt jede Anweisung einzeln aus.
    Inline-Kommentare (-- bis Zeilenende) werden per Regex entfernt,
    bevor nach Semikolon aufgespalten wird. Das ist robuster als nur
    Zeilen zu ueberspringen, die mit -- beginnen, weil es auch
    Kommentare am Ende einer Anweisung korrekt behandelt.
    Hinweis: psycopg3 unterstuetzt keine mehreren Anweisungen in einem
    einzigen cur.execute()-Aufruf (Breaking Change gegenueber psycopg2).
    """
    if not pfad.exists():
        print(f"Fehler: SQL-Datei nicht gefunden: {pfad}")
        raise FileNotFoundError(pfad)
    print(f"  Fuehre {bezeichnung} aus ...")
    sql_text = pfad.read_text(encoding="utf-8")
    sql_clean = re.sub(r"--[^\n]*", "", sql_text)   # alle Inline-Kommentare entfernen
    for anweisung in sql_clean.split(";"):
        anweisung = anweisung.strip()
        if anweisung:
            cur.execute(anweisung)


# --- Hilfsfunktion: JSONL blockweise laden ------------------------------

def jsonl_laden(pfad, collection, bezeichnung, drop=False):
    """
    Liest eine JSONL-Datei zeilenweise und schreibt sie in Bloecken
    in die angegebene MongoDB-Collection.
    """
    if not pfad.exists():
        print(f"Fehler: Datei nicht gefunden: {pfad}")
        sys.exit(1)
    if drop:
        collection.drop()

    puffer = []
    zaehler = 0
    print(f"  Lade {bezeichnung} ...")
    with open(pfad, encoding="utf-8") as f:
        for zeile in f:
            zeile = zeile.strip()
            if not zeile:
                continue
            puffer.append(json.loads(zeile))
            zaehler += 1
            if len(puffer) >= BLOCK_GROESSE:
                try:
                    collection.insert_many(puffer)
                except Exception as e:
                    print(f"Fehler beim Schreiben in {bezeichnung}: {e}")
                    print("Hinweis: Bei bereits vorhandenen Daten --drop angeben.")
                    sys.exit(1)
                puffer.clear()
            if zaehler % FORTSCHRITT_ALLE == 0:
                print(f"    {zaehler:>10,} Dokumente ...")
    if puffer:
        collection.insert_many(puffer)
    print(f"    {zaehler:>10,} Dokumente geladen.")
    return zaehler


# --- Arm A: PostgreSQL --------------------------------------------------

def lade_arm_a(datenpfad, drop):
    try:
        import psycopg
    except ImportError:
        print("Fehler: psycopg ist nicht installiert (pip install 'psycopg[binary]').")
        sys.exit(1)

    try:
        conn = psycopg.connect(
            f"host={PG_HOST} port={PG_PORT} dbname={PG_DBNAME} "
            f"user={PG_USER} password={PG_PASSWORD}",
            autocommit=True)
    except psycopg.OperationalError as e:
        print(f"Fehler: PostgreSQL unter {PG_HOST}:{PG_PORT} nicht erreichbar.")
        print(f"  Details: {e}")
        sys.exit(1)

    beginn = time.monotonic()

    # 1. Schema: Tabellen anlegen (enthaelt DROP TABLE IF EXISTS, deckt --drop ab).
    try:
        with conn.cursor() as cur:
            sql_ausfuehren(cur, SCHEMA_PFAD, "Schema (Tabellen)")
    except FileNotFoundError:
        conn.close()
        sys.exit(1)

    # 2. Daten laden: COPY streamt CSV direkt in PostgreSQL, kein INSERT je Zeile.
    csv_pfad = Path(datenpfad) / "csv"
    tabellen = [
        ("kunde",            csv_pfad / "kunde.csv"),
        ("produkt",          csv_pfad / "produkt.csv"),
        ("auftrag",          csv_pfad / "auftrag.csv"),
        ("auftragsposition", csv_pfad / "auftragsposition.csv"),
    ]

    for tabelle, pfad in tabellen:
        if not pfad.exists():
            print(f"Fehler: CSV-Datei nicht gefunden: {pfad}")
            conn.close()
            sys.exit(1)
        print(f"  COPY {tabelle} ...")
        with conn.cursor() as cur:
            with cur.copy(f"COPY {tabelle} FROM STDIN (FORMAT CSV)") as copy:
                with open(pfad, "rb") as f:
                    while chunk := f.read(65_536):
                        copy.write(chunk)

    # 3. Sekundaerindizes nach dem Laden aufbauen -- schneller als waehrend des
    #    Ladens, weil PostgreSQL den B-Tree in einem Durchlauf sortiert bauen kann.
    try:
        with conn.cursor() as cur:
            sql_ausfuehren(cur, INDIZES_PFAD, "Indizes")
    except FileNotFoundError:
        conn.close()
        sys.exit(1)

    # 4. ANALYZE: aktualisiert die Zeilenstatistiken, auf die der Query-Planner
    #    fuer Join-Reihenfolge und Index-Entscheidungen angewiesen ist.
    #    Ohne ANALYZE nach einem Bulk-Load sind die Statistiken veraltet und
    #    Arm A koennte kuenstlich langsame Plaene erhalten.
    print("  ANALYZE ...")
    t_analyze = time.monotonic()
    with conn.cursor() as cur:
        cur.execute("ANALYZE")
    dauer_analyze = time.monotonic() - t_analyze
    print(f"    ANALYZE fertig in {dauer_analyze:.1f} s.")

    # 5. Zeilenzahlen fuer die spaetere Pruefung abfragen.
    anzahlen = {}
    with conn.cursor() as cur:
        for tabelle, _ in tabellen:
            cur.execute(f"SELECT COUNT(*) FROM {tabelle}")
            anzahlen[tabelle] = cur.fetchone()[0]
    conn.close()

    dauer = time.monotonic() - beginn
    print(f"  Arm A fertig in {dauer:.1f} s (davon ANALYZE: {dauer_analyze:.1f} s).")
    return anzahlen, dauer


# --- Arm B: MongoDB, referenziert ---------------------------------------

def lade_arm_b(datenpfad, drop):
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        print("Fehler: pymongo ist nicht installiert (pip install pymongo).")
        sys.exit(1)

    try:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        client.server_info()
    except Exception:
        print(f"Fehler: MongoDB unter {MONGO_URL} nicht erreichbar.")
        sys.exit(1)

    beginn = time.monotonic()
    db = client["arm_b"]
    json_pfad = Path(datenpfad) / "json"

    anzahlen = {}
    anzahlen["kunden"]    = jsonl_laden(json_pfad / "kunden.jsonl",
                                         db.kunden,    "kunden (arm_b)",    drop)
    anzahlen["produkte"]  = jsonl_laden(json_pfad / "produkte.jsonl",
                                         db.produkte,  "produkte (arm_b)",  drop)
    anzahlen["auftraege"] = jsonl_laden(json_pfad / "auftraege_ref.jsonl",
                                         db.auftraege, "auftraege (arm_b)", drop)
    anzahlen["positionen"]= jsonl_laden(json_pfad / "positionen.jsonl",
                                         db.positionen,"positionen (arm_b)",drop)

    client.close()
    dauer = time.monotonic() - beginn
    print(f"  Arm B fertig in {dauer:.1f} s.")
    return anzahlen, dauer


# --- Arm C: MongoDB, eingebettet ----------------------------------------

def lade_arm_c(datenpfad, drop):
    try:
        from pymongo import MongoClient
    except ImportError:
        print("Fehler: pymongo ist nicht installiert (pip install pymongo).")
        sys.exit(1)

    try:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        client.server_info()
    except Exception:
        print(f"Fehler: MongoDB unter {MONGO_URL} nicht erreichbar.")
        sys.exit(1)

    beginn = time.monotonic()
    db = client["arm_c"]
    json_pfad = Path(datenpfad) / "json"

    anzahlen = {}
    anzahlen["kunden"]    = jsonl_laden(json_pfad / "kunden.jsonl",
                                         db.kunden,    "kunden (arm_c)",    drop)
    anzahlen["produkte"]  = jsonl_laden(json_pfad / "produkte.jsonl",
                                         db.produkte,  "produkte (arm_c)",  drop)
    anzahlen["auftraege"] = jsonl_laden(json_pfad / "auftraege_emb.jsonl",
                                         db.auftraege, "auftraege (arm_c)", drop)

    # Positionen sind in den Auftragsdokumenten eingebettet; ihre Gesamtzahl
    # ergibt sich aus der Summe der Array-Laengen ueber alle Dokumente.
    ergebnis = list(db.auftraege.aggregate([
        {"$group": {"_id": None,
                    "gesamt": {"$sum": {"$size": "$positionen"}}}}
    ]))
    anzahlen["positionen"] = ergebnis[0]["gesamt"] if ergebnis else 0

    client.close()
    dauer = time.monotonic() - beginn
    print(f"  Arm C fertig in {dauer:.1f} s.")
    return anzahlen, dauer


# --- Pruefung -----------------------------------------------------------

def pruefen(soll, ist_a, ist_b, ist_c, arme):
    """
    Vergleicht geladene Anzahlen mit den Sollwerten aus kennzahlen.json.
    Gibt True zurueck, wenn es Abweichungen gibt.
    """
    # (Anzeigebezeichnung, Soll-Schluessel, A-Schluessel, B-Schluessel, C-Schluessel)
    pruefpunkte = [
        ("Kunden",     "kunden",     "kunde",             "kunden",    "kunden"),
        ("Produkte",   "produkte",   "produkt",           "produkte",  "produkte"),
        ("Auftraege",  "auftraege",  "auftrag",           "auftraege", "auftraege"),
        ("Positionen", "positionen", "auftragsposition",  "positionen","positionen"),
    ]

    arm_schluessel = {"A": 2, "B": 3, "C": 4}
    B = 14  # Spaltenbreite fuer Zahlenwerte

    # Kopfzeile
    print(f"\n{'Entitaet':<14} {'Soll':>{B}}", end="")
    for arm in arme:
        print(f"  {'Arm ' + arm:>{B}}", end="")
    print()
    print("-" * (14 + (B + 2) * (1 + len(arme))))

    fehler = False
    for name, soll_key, key_a, key_b, key_c in pruefpunkte:
        soll_wert = soll[soll_key]
        print(f"{name:<14} {soll_wert:>{B},}", end="")
        for arm in arme:
            ist_map = {"A": ist_a, "B": ist_b, "C": ist_c}[arm]
            ist_key = {"A": key_a, "B": key_b, "C": key_c}[arm]
            wert = ist_map.get(ist_key, 0)
            abw = wert - soll_wert
            markierung = " !!!" if abw != 0 else "    "
            print(f"  {wert:>{B},}{markierung}", end="")
            if abw != 0:
                fehler = True
        print()

    if fehler:
        print("\nACHTUNG: Abweichungen gefunden -- Laden wiederholen oder Daten pruefen.")
    else:
        print("\nAlle Anzahlen stimmen ueberein.")
    return fehler


# --- Hauptlauf ----------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Laedt erzeugte Testdaten in die drei Datenbanken.")
    p.add_argument("--daten", required=True,
                   help="Pfad zum Datenverzeichnis, z. B. daten/sf1")
    p.add_argument("--arm", choices=["A", "B", "C", "alle"], default="alle",
                   help="Welcher Messarm geladen werden soll (Standard: alle)")
    p.add_argument("--drop", action="store_true",
                   help="Vorhandene Daten vor dem Laden loeschen")
    args = p.parse_args()

    datenpfad = Path(args.daten)
    if not datenpfad.exists():
        print(f"Fehler: Datenverzeichnis nicht gefunden: {datenpfad}")
        sys.exit(1)

    kennzahlen_pfad = datenpfad / "kennzahlen.json"
    if not kennzahlen_pfad.exists():
        print(f"Fehler: kennzahlen.json nicht gefunden in {datenpfad}")
        sys.exit(1)
    soll = json.loads(kennzahlen_pfad.read_text(encoding="utf-8"))

    arme = ["A", "B", "C"] if args.arm == "alle" else [args.arm]

    ist_a, ist_b, ist_c = {}, {}, {}
    dauer_a = dauer_b = dauer_c = 0.0

    if "A" in arme:
        print("\n=== Arm A: PostgreSQL ===")
        ist_a, dauer_a = lade_arm_a(datenpfad, args.drop)

    if "B" in arme:
        print("\n=== Arm B: MongoDB, referenziert ===")
        ist_b, dauer_b = lade_arm_b(datenpfad, args.drop)

    if "C" in arme:
        print("\n=== Arm C: MongoDB, eingebettet ===")
        ist_c, dauer_c = lade_arm_c(datenpfad, args.drop)

    print("\n=== Ladezeiten ===")
    if "A" in arme:
        print(f"  Arm A: {dauer_a:.1f} s")
    if "B" in arme:
        print(f"  Arm B: {dauer_b:.1f} s")
    if "C" in arme:
        print(f"  Arm C: {dauer_c:.1f} s")

    print("\n=== Pruefung ===")
    fehler = pruefen(soll, ist_a, ist_b, ist_c, arme)

    if "B" in arme or "C" in arme:
        print("\nHinweis: Indizes fuer MongoDB jetzt anlegen:")
        print("  python skripte/mongo_setup.py")

    sys.exit(1 if fehler else 0)


if __name__ == "__main__":
    main()
