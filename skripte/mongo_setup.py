"""
Legt die Indizes fuer Arm B (arm_b) und Arm C (arm_c) an.

Wiederholt ausfuehrbar: pymongo's create_index ist idempotent --
ein Index mit identischer Schluesseldefinition und identischem Namen
wird nicht doppelt angelegt.

Ausfuehren, nachdem die Daten geladen wurden:
    python skripte/mongo_setup.py
"""

import os
import sys

from pymongo import ASCENDING, MongoClient
from pymongo.errors import ServerSelectionTimeoutError

# Host und Port aus Umgebungsvariablen wie in bench.py; Standard ist localhost.
MONGO_URL = (f"mongodb://{os.environ.get('MONGO_HOST', 'localhost')}"
             f":{os.environ.get('MONGO_PORT', '27017')}")


def indizes_ausgeben(db, collection_name):
    """Gibt alle vorhandenen Indizes einer Collection aus."""
    print(f"  {db.name}.{collection_name}:")
    for idx in db[collection_name].list_indexes():
        print(f"    {idx['name']}: {dict(idx['key'])}")


def main():
    try:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        # Verbindung aktiv pruefen -- MongoClient verbindet sich erst beim
        # ersten echten Aufruf, daher server_info() als Fruehtest.
        client.server_info()
    except ServerSelectionTimeoutError:
        print(f"Fehler: MongoDB ist unter {MONGO_URL} nicht erreichbar.")
        print("Bitte sicherstellen, dass der MongoDB-Container laeuft.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Arm B: arm_b, referenziertes Modell
    # Collections: auftraege, positionen, kunden, produkte
    #
    # Auf _id legt MongoDB automatisch einen Index an -- diese Zugriffe
    # (z. B. kunden.find_one({"_id": ...})) brauchen keinen eigenen Index.
    # ------------------------------------------------------------------
    db_b = client["arm_b"]

    # L1, Q1: Positionen eines Auftrags suchen.
    # positionen.find({"auftrag_id": auftrag_id}) in l1 und q1.
    db_b.positionen.create_index(
        [("auftrag_id", ASCENDING)],
        name="idx_positionen_auftrag_id")

    # Q1: Positionen nach Produkt aggregieren.
    # Entspricht idx_auftragsposition_produkt_id in Arm A.
    # Arm B rechnet den Umsatz clientseitig, iteriert dabei ueber
    # positionen.produkt_id -- der Index ermoeglicht kuenftige
    # produktbezogene Abfragen und haelt den Vergleich fair.
    db_b.positionen.create_index(
        [("produkt_id", ASCENDING)],
        name="idx_positionen_produkt_id")

    # L3, L4: Auftraege eines Kunden bzw. eines Zeitraums finden.
    # auftraege.find({"bestelldatum": ...}) in q1 und q2.
    # Entspricht idx_auftrag_kunde_id und idx_auftrag_bestelldatum in Arm A.
    db_b.auftraege.create_index(
        [("kunde_id", ASCENDING)],
        name="idx_auftraege_kunde_id")

    db_b.auftraege.create_index(
        [("bestelldatum", ASCENDING)],
        name="idx_auftraege_bestelldatum")

    # ------------------------------------------------------------------
    # Arm C: arm_c, eingebettetes Modell
    # Collection auftraege enthaelt Positionen und Kundenanschrift
    # als eingebettete Felder. kunden und produkte als Stammdaten.
    #
    # Auf _id legt MongoDB automatisch einen Index an.
    # ------------------------------------------------------------------
    db_c = client["arm_c"]

    # L3: update_many sucht Auftragsdokumente ueber die eingebettete
    # Kundenkennung: auftraege.update_many({"kunde.kunde_id": kunde_id}).
    db_c.auftraege.create_index(
        [("kunde.kunde_id", ASCENDING)],
        name="idx_auftraege_kunde_kunde_id")

    # L4 (Q1, Q2): Auftraege eines Zeitraums filtern.
    # $match-Stufe in der Aggregations-Pipeline und find() in q2.
    # Entspricht idx_auftrag_bestelldatum in Arm A und B.
    db_c.auftraege.create_index(
        [("bestelldatum", ASCENDING)],
        name="idx_auftraege_bestelldatum")

    # Q1: Aggregations-Pipeline entfaltet positionen per $unwind und
    # gruppiert nach positionen.produkt_id. Der Index beschleunigt
    # Abfragen, die direkt auf dieses eingebettete Feld filtern, und
    # stellt sicher, dass Arm C denselben Zugriffsweg abgedeckt hat
    # wie Arm A (idx_auftragsposition_produkt_id) und Arm B.
    db_c.auftraege.create_index(
        [("positionen.produkt_id", ASCENDING)],
        name="idx_auftraege_positionen_produkt_id")

    # ------------------------------------------------------------------
    # Vorhandene Indizes ausgeben zur Pruefung
    # ------------------------------------------------------------------
    print("\n=== Arm B (arm_b) ===")
    for col in ["auftraege", "positionen", "kunden", "produkte"]:
        indizes_ausgeben(db_b, col)

    print("\n=== Arm C (arm_c) ===")
    for col in ["auftraege", "kunden", "produkte"]:
        indizes_ausgeben(db_c, col)

    client.close()


if __name__ == "__main__":
    main()
