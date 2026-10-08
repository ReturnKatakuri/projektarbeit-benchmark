"""
Datengenerator fuer den Praxisteil.

Erzeugt aus EINEM Durchlauf den Datenbestand fuer alle drei Messarme:
  Arm A (PostgreSQL, normalisiert) -> CSV-Dateien
  Arm B (MongoDB, referenziert)    -> JSON-Lines, vier Collections
  Arm C (MongoDB, eingebettet)     -> JSON-Lines, ein Auftragsdokument je Auftrag

Beispiel:
    python generate_data.py --orders 200000 --out daten/sf1
    python generate_data.py --orders 3000000 --out daten/sf2
"""

import argparse
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

# --- Feste Vorgaben aus Abschnitt 3.2.3 ---------------------------------

FANOUT_STUFEN = [10, 100, 1000]   # Auftraege je Kunde in den drei Gruppen
KUNDEN_JE_GRUPPE = 50
NORMALKUNDE_MIN, NORMALKUNDE_MAX = 1, 20

# Auftragsklassen: (Anteil, min Positionen, max Positionen)
AUFTRAGSKLASSEN = [(0.60, 1, 3), (0.30, 10, 20), (0.10, 20, 60)]

ZIPF_S = 1.0                      # Steilheit der Produktverteilung
START_DATUM = date(2024, 1, 1)
TAGE = 730                        # zwei Jahre

ORTE = [("10115", "Berlin"), ("20095", "Hamburg"), ("45127", "Essen"),
        ("80331", "Muenchen"), ("50667", "Koeln"), ("70173", "Stuttgart"),
        ("04109", "Leipzig"), ("30159", "Hannover")]
WARENGRUPPEN = ["Kaffeemuehle", "Wasserkocher", "Pfanne", "Messer", "Toaster",
                "Kochtopf", "Schneidebrett", "Mixer", "Waage", "Thermoskanne"]


# --- Hilfsfunktionen ----------------------------------------------------

def zipf_gewichte(n, s=ZIPF_S):
    """Gewichte 1/rang^s, kumuliert fuer random.choices."""
    gewichte = [1.0 / ((i + 1) ** s) for i in range(n)]
    kumuliert, summe = [], 0.0
    for g in gewichte:
        summe += g
        kumuliert.append(summe)
    return kumuliert


def positionszahl(rng):
    """Zieht die Positionszahl gemaess der drei Auftragsklassen."""
    wurf = rng.random()
    grenze = 0.0
    for anteil, pmin, pmax in AUFTRAGSKLASSEN:
        grenze += anteil
        if wurf <= grenze:
            return rng.randint(pmin, pmax)
    return rng.randint(1, 3)


def auftragszahlen(anzahl_auftraege, rng):
    """
    Verteilt die Auftraege auf Kunden.
    Rueckgabe: Liste (kunde_id, anzahl_auftraege), Kohorten zuerst.
    """
    verteilung, kunde_id = [], 1
    kohorten = {}
    for stufe in FANOUT_STUFEN:
        ids = []
        for _ in range(KUNDEN_JE_GRUPPE):
            verteilung.append((kunde_id, stufe))
            ids.append(kunde_id)
            kunde_id += 1
        kohorten[stufe] = ids

    vergeben = sum(n for _, n in verteilung)
    if vergeben > anzahl_auftraege:
        raise SystemExit(
            f"--orders muss mindestens {vergeben} betragen, damit die "
            f"Fan-out-Gruppen vollstaendig sind.")

    while vergeben < anzahl_auftraege:
        n = min(rng.randint(NORMALKUNDE_MIN, NORMALKUNDE_MAX),
                anzahl_auftraege - vergeben)
        verteilung.append((kunde_id, n))
        kunde_id += 1
        vergeben += n
    return verteilung, kohorten


# --- Hauptlauf ----------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--orders", type=int, default=200000, help="Anzahl Auftraege")
    p.add_argument("--products", type=int, default=5000, help="Anzahl Produkte")
    p.add_argument("--out", default="daten", help="Zielverzeichnis")
    p.add_argument("--seed", type=int, default=20260921, help="Zufallssaat")
    p.add_argument("--keys", type=int, default=50000,
                   help="Anzahl vorgezogener Auftragsnummern fuer L1")
    args = p.parse_args()

    rng = random.Random(args.seed)
    ziel = Path(args.out)
    (ziel / "csv").mkdir(parents=True, exist_ok=True)
    (ziel / "json").mkdir(parents=True, exist_ok=True)

    # 1. Produkte
    produkte = []
    for pid in range(1, args.products + 1):
        produkte.append({
            "produkt_id": pid,
            "bezeichnung": f"{rng.choice(WARENGRUPPEN)} Modell {pid}",
            "preis": round(rng.uniform(4.99, 249.99), 2),
        })
    kumuliert = zipf_gewichte(len(produkte))

    # 2. Kunden
    verteilung, kohorten = auftragszahlen(args.orders, rng)
    kunden = []
    for kunde_id, _ in verteilung:
        plz, ort = rng.choice(ORTE)
        kunden.append({
            "kunde_id": kunde_id,
            "name": f"Kunde {kunde_id}",
            "strasse": f"Musterweg {rng.randint(1, 199)}",
            "plz": plz,
            "ort": ort,
        })
    kunden_nach_id = {k["kunde_id"]: k for k in kunden}

    # 3. Stammdaten schreiben
    with open(ziel / "csv" / "produkt.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for pr in produkte:
            w.writerow([pr["produkt_id"], pr["bezeichnung"], pr["preis"]])
    with open(ziel / "csv" / "kunde.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for k in kunden:
            w.writerow([k["kunde_id"], k["name"], k["strasse"], k["plz"], k["ort"]])

    def jsonl(pfad, zeilen):
        with open(pfad, "w", encoding="utf-8") as f:
            for z in zeilen:
                f.write(json.dumps(z, ensure_ascii=False) + "\n")

    jsonl(ziel / "json" / "produkte.jsonl",
          ({**pr, "_id": pr["produkt_id"]} for pr in produkte))
    jsonl(ziel / "json" / "kunden.jsonl",
          ({**k, "_id": k["kunde_id"]} for k in kunden))

    # 4. Auftraege und Positionen, stroemend geschrieben
    f_auftrag = open(ziel / "csv" / "auftrag.csv", "w", newline="", encoding="utf-8")
    f_pos = open(ziel / "csv" / "auftragsposition.csv", "w", newline="", encoding="utf-8")
    f_ref = open(ziel / "json" / "auftraege_ref.jsonl", "w", encoding="utf-8")
    f_refpos = open(ziel / "json" / "positionen.jsonl", "w", encoding="utf-8")
    f_emb = open(ziel / "json" / "auftraege_emb.jsonl", "w", encoding="utf-8")
    w_auftrag, w_pos = csv.writer(f_auftrag), csv.writer(f_pos)

    auftrag_id, positionen_gesamt = 0, 0
    position_id = 0
    for kunde_id, anzahl in verteilung:
        kunde = kunden_nach_id[kunde_id]
        for _ in range(anzahl):
            auftrag_id += 1
            datum = (START_DATUM + timedelta(days=rng.randint(0, TAGE - 1))).isoformat()

            w_auftrag.writerow([auftrag_id, kunde_id, datum])
            f_ref.write(json.dumps(
                {"_id": auftrag_id, "kunde_id": kunde_id, "bestelldatum": datum}) + "\n")

            eingebettet = []
            for nr in range(1, positionszahl(rng) + 1):
                pr = produkte[bisect(kumuliert, rng.random() * kumuliert[-1])]
                menge = rng.randint(1, 5)
                position_id += 1
                positionen_gesamt += 1

                w_pos.writerow([auftrag_id, nr, pr["produkt_id"], menge, pr["preis"]])
                f_refpos.write(json.dumps({
                    "_id": position_id, "auftrag_id": auftrag_id, "positionsnr": nr,
                    "produkt_id": pr["produkt_id"], "menge": menge,
                    "einzelpreis": pr["preis"]}) + "\n")
                eingebettet.append({
                    "positionsnr": nr, "produkt_id": pr["produkt_id"],
                    "bezeichnung": pr["bezeichnung"], "menge": menge,
                    "einzelpreis": pr["preis"]})

            f_emb.write(json.dumps({
                "_id": auftrag_id,
                "bestelldatum": datum,
                "kunde": {"kunde_id": kunde_id, "name": kunde["name"],
                          "strasse": kunde["strasse"], "plz": kunde["plz"],
                          "ort": kunde["ort"]},
                "positionen": eingebettet}, ensure_ascii=False) + "\n")

    for f in (f_auftrag, f_pos, f_ref, f_refpos, f_emb):
        f.close()

    # 5. Feste Abfrageliste fuer L1 und die Kundengruppen fuer L3
    anzahl_keys = min(args.keys, auftrag_id)
    keys = rng.sample(range(1, auftrag_id + 1), anzahl_keys)
    (ziel / "abfrage_ids.txt").write_text(
        "\n".join(str(k) for k in keys), encoding="utf-8")
    (ziel / "fanout_kunden.json").write_text(
        json.dumps({str(s): ids for s, ids in kohorten.items()}, indent=2),
        encoding="utf-8")

    # 6. Kennzahlen zur Pruefung nach dem Laden
    kennzahlen = {
        "seed": args.seed,
        "kunden": len(kunden),
        "produkte": len(produkte),
        "auftraege": auftrag_id,
        "positionen": positionen_gesamt,
        "positionen_je_auftrag": round(positionen_gesamt / auftrag_id, 2),
        "fanout_gruppen": {str(s): len(ids) for s, ids in kohorten.items()},
        "abfrage_ids": anzahl_keys,
    }
    (ziel / "kennzahlen.json").write_text(
        json.dumps(kennzahlen, indent=2), encoding="utf-8")
    print(json.dumps(kennzahlen, indent=2))


def bisect(kumuliert, wert):
    """Binaere Suche: liefert den Index des gezogenen Produkts."""
    lo, hi = 0, len(kumuliert) - 1
    while lo < hi:
        mitte = (lo + hi) // 2
        if kumuliert[mitte] < wert:
            lo = mitte + 1
        else:
            hi = mitte
    return lo


if __name__ == "__main__":
    main()