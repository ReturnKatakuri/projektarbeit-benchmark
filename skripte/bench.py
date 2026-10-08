"""
Messskript fuer einen einzelnen Messlauf (Abschnitt 3.4 der Arbeit).

Ablauf: Aufwaermphase -> Messfenster -> Ergebniszeile in die CSV-Datei.

Jeder Client ist ein eigener Prozess mit eigener Datenbankverbindung. Als
Threads in einem Prozess wuerden sich die Clients wegen des Global Interpreter
Lock gegenseitig ausbremsen; die Wartezeit auf den Lock stuende dann in den
gemessenen Antwortzeiten.

Beispiel:
    python skripte\\bench.py --arm C --profil L1 --sf sf1 --lauf 1
    python skripte\\bench.py --arm C --profil L3 --sf sf1 --fanout 100 --lauf 1
    python skripte\\bench.py --arm A --profil L1 --sf sf1 --lauf 1 --probelauf
"""

import argparse
import csv
import json
import multiprocessing
import os
import queue
import random
import statistics
import sys
import threading                 # nur fuer BrokenBarrierError
import time
from datetime import date, timedelta
from pathlib import Path

from queries import ARME

ERGEBNISDATEI = Path("ergebnisse") / "messungen.csv"
SPALTEN = ["zeitpunkt", "arm", "profil", "sf", "fanout", "lauf", "clients",
           "dauer_s", "operationen", "fehler", "median_ms", "p95_ms",
           "durchsatz_ops", "geschrieben_schnitt"]


# --- Vorbereitung -------------------------------------------------------

def verbinde(arm):
    """
    Verbindung fuer einen Arm oeffnen. Host und Port kommen aus
    Umgebungsvariablen; ohne sie gilt localhost mit den Standardports.
    """
    if arm == "A":
        return ARME[arm](host=os.environ.get("PG_HOST", "localhost"),
                         port=int(os.environ.get("PG_PORT", "5432")))
    host = os.environ.get("MONGO_HOST", "localhost")
    port = os.environ.get("MONGO_PORT", "27017")
    return ARME[arm](url=f"mongodb://{host}:{port}")


def lade_daten(pfad):
    """Abfrage-IDs, Fan-out-Kunden und Kennzahlen des Bestands einlesen."""
    p = Path(pfad)
    ids = [int(z) for z in (p / "abfrage_ids.txt").read_text().split()]
    fanout = json.loads((p / "fanout_kunden.json").read_text())
    kennzahlen = json.loads((p / "kennzahlen.json").read_text())
    return ids, fanout, kennzahlen


def zufallsadresse(rng):
    orte = [("10115", "Berlin"), ("20095", "Hamburg"), ("45127", "Essen"),
            ("80331", "Muenchen"), ("50667", "Koeln")]
    plz, ort = rng.choice(orte)
    return f"Neue Strasse {rng.randint(1, 199)}", plz, ort


def baue_operation(profil, arm, ids, fanout, kennzahlen, args, zaehler, startwert):
    """Liefert eine Funktion, die genau eine Operation ausfuehrt."""
    # Fester Startwert: Bei gleicher Laufnummer zieht derselbe Client in allen
    # drei Armen dieselbe Folge von Auftraegen, Kunden und Monaten.
    rng = random.Random(startwert)
    produkte = kennzahlen["produkte"]
    kunden = kennzahlen["kunden"]

    if profil == "L1":
        def op():
            return arm.l1_auftrag_lesen(rng.choice(ids)), 0

    elif profil == "L2":
        def op():
            with zaehler.get_lock():           # eindeutige neue Auftragsnummer
                zaehler.value += 1
                neue_id = zaehler.value
            anzahl = rng.randint(1, 3)
            positionen = [(nr, rng.randint(1, produkte), rng.randint(1, 5),
                           round(rng.uniform(4.99, 249.99), 2))
                          for nr in range(1, anzahl + 1)]
            datum = date.today().isoformat()
            geschrieben = arm.l2_auftrag_anlegen(neue_id, rng.randint(1, kunden),
                                                 datum, positionen)
            return 0, geschrieben

    elif profil == "L3":
        gruppe = fanout[str(args.fanout)]
        def op():
            strasse, plz, ort = zufallsadresse(rng)
            geschrieben = arm.l3_adresse_aendern(rng.choice(gruppe), strasse, plz, ort)
            return 0, geschrieben

    elif profil == "L4":
        def op():
            jahr, monat = 2024, rng.randint(1, 12)
            if args.abfrage == "Q1":
                return arm.q1_umsatz_je_produkt(jahr, monat), 0
            return arm.q2_auftragsliste(jahr, monat), 0
    else:
        raise SystemExit(f"Unbekanntes Profil: {profil}")

    return op


# --- Messung ------------------------------------------------------------

def arbeiter(op, ende):
    """Fuehrt Operationen aus, bis der Zeitpunkt 'ende' erreicht ist."""
    lokal_zeiten, lokal_geschrieben, lokal_fehler = [], [], 0
    while time.monotonic() < ende:
        start = time.perf_counter()
        try:
            _, anzahl = op()
            lokal_zeiten.append((time.perf_counter() - start) * 1000)
            lokal_geschrieben.append(anzahl)
        except Exception as e:
            lokal_fehler += 1
            if lokal_fehler == 1:          # nur den ersten Fehler je Client melden
                print(f"Fehler: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return lokal_zeiten, lokal_geschrieben, lokal_fehler


def client_prozess(nummer, args, ids, fanout, kennzahlen, zaehler, schranke, ergebnisse):
    """
    Ein Client als eigener Prozess: verbinden, auf den gemeinsamen Start
    warten, aufwaermen, messen. Schickt in jedem Fall genau eine Nachricht
    an den Hauptprozess, damit dieser nie vergeblich wartet.
    """
    try:
        verbindung = verbinde(args.arm)
        op = baue_operation(args.profil, verbindung, ids, fanout, kennzahlen,
                            args, zaehler, startwert=args.lauf * 100 + nummer)
    except Exception as e:
        ergebnisse.put(("startfehler", f"{type(e).__name__}: {e}"))
        schranke.abort()                 # die anderen Clients nicht warten lassen
        return

    try:
        schranke.wait()                  # alle Clients starten gleichzeitig
    except threading.BrokenBarrierError:
        verbindung.close()
        ergebnisse.put(("abgebrochen", None))
        return

    arbeiter(op, time.monotonic() + args.aufwaermen)     # Aufwaermen, Werte verwerfen
    beginn = time.monotonic()
    zeiten, geschrieben, fehler = arbeiter(op, beginn + args.dauer)
    gemessen = time.monotonic() - beginn
    verbindung.close()
    ergebnisse.put(("ergebnis", (zeiten, geschrieben, fehler, gemessen)))


def messe(args, ids, fanout, kennzahlen):
    """
    Startet je Client einen Prozess und fuehrt deren Messwerte zusammen.
    Liefert Antwortzeiten, Fehlerzahl, geschriebene Anzahlen und die Dauer
    des Messfensters (laengster Client).
    """
    # Gemeinsamer Zaehler fuer neue Auftragsnummern in L2, mit Platz ueber dem Bestand
    zaehler = multiprocessing.Value("q", kennzahlen["auftraege"] + 1_000_000)
    schranke = multiprocessing.Barrier(args.clients + 1)     # Clients + Hauptprozess
    ergebnisse = multiprocessing.Queue()

    prozesse = [multiprocessing.Process(
                    target=client_prozess, daemon=True,
                    args=(nummer, args, ids, fanout, kennzahlen, zaehler, schranke,
                          ergebnisse))
                for nummer in range(args.clients)]
    for pr in prozesse:
        pr.start()

    try:
        schranke.wait(timeout=120)       # warten, bis alle Clients verbunden sind
    except threading.BrokenBarrierError:
        pass                             # Startfehler; die Meldungen kommen unten an
    else:
        print(f"Aufwaermen {args.aufwaermen} s ...", flush=True)
        time.sleep(args.aufwaermen)
        print(f"Messung {args.dauer} s ...", flush=True)

    # Erst die Nachrichten abholen, dann auf das Prozessende warten: Ein Prozess
    # endet erst, wenn seine Messwerte aus der Warteschlange gelesen sind.
    meldungen = []
    while len(meldungen) < args.clients:
        try:
            meldungen.append(ergebnisse.get(timeout=1))
        except queue.Empty:
            if not any(pr.is_alive() for pr in prozesse):
                break                    # ein Client ist ohne Nachricht beendet worden
    for pr in prozesse:
        pr.join(timeout=10)

    for art, inhalt in meldungen:
        if art == "startfehler":
            print(f"Fehler beim Verbinden: {inhalt}", file=sys.stderr, flush=True)
    werte = [inhalt for art, inhalt in meldungen if art == "ergebnis"]
    if len(werte) < args.clients:
        raise SystemExit(f"Abbruch: nur {len(werte)} von {args.clients} Clients "
                         f"haben Messwerte geliefert.")

    zeiten, geschrieben, fehler, gemessen = [], [], 0, 0.0
    for z, g, f, dauer in werte:
        zeiten.extend(z)
        geschrieben.extend(g)
        fehler += f
        gemessen = max(gemessen, dauer)
    return zeiten, fehler, geschrieben, gemessen


def perzentil(werte, anteil):
    if not werte:
        return 0.0
    geordnet = sorted(werte)
    index = min(int(len(geordnet) * anteil), len(geordnet) - 1)
    return geordnet[index]


# --- Hauptlauf ----------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--arm", choices=["A", "B", "C"], required=True)
    p.add_argument("--profil", choices=["L1", "L2", "L3", "L4"], required=True,
                   help="Lastprofil. L2 und L3 werden nur auf SF1 gemessen.")
    p.add_argument("--sf", default="sf1", help="Bezeichnung der Datenmenge")
    p.add_argument("--daten", default=None, help="Pfad zum Datenverzeichnis")
    p.add_argument("--fanout", type=int, default=0, choices=[0, 10, 100, 1000])
    p.add_argument("--abfrage", choices=["Q1", "Q2"], default="Q1")
    p.add_argument("--lauf", type=int, default=1)
    p.add_argument("--clients", type=int, default=8)
    p.add_argument("--aufwaermen", type=int, default=60)
    p.add_argument("--dauer", type=int, default=120)
    p.add_argument("--probelauf", action="store_true",
                   help="5 s aufwaermen, 10 s messen, 2 Clients")
    args = p.parse_args()

    if args.probelauf:
        args.aufwaermen, args.dauer, args.clients = 5, 10, 2
    if args.profil == "L3" and args.fanout == 0:
        raise SystemExit("Bei L3 muss --fanout 10, 100 oder 1000 angegeben werden.")
    datenpfad = args.daten or str(Path("daten") / args.sf)

    ids, fanout, kennzahlen = lade_daten(datenpfad)

    # Je Client ein eigener Prozess mit eigener Verbindung
    zeiten, fehler, geschrieben, gemessen = messe(args, ids, fanout, kennzahlen)

    anzahl = len(zeiten)
    zeile = {
        "zeitpunkt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "arm": args.arm,
        "profil": args.profil if args.profil != "L4" else f"L4-{args.abfrage}",
        "sf": args.sf,
        "fanout": args.fanout,
        "lauf": args.lauf,
        "clients": args.clients,
        "dauer_s": round(gemessen, 1),
        "operationen": anzahl,
        "fehler": fehler,
        "median_ms": round(statistics.median(zeiten), 3) if zeiten else 0,
        "p95_ms": round(perzentil(zeiten, 0.95), 3),
        "durchsatz_ops": round(anzahl / gemessen, 1) if gemessen else 0,
        "geschrieben_schnitt": round(sum(geschrieben) / len(geschrieben), 1)
                               if geschrieben else 0,
    }

    ERGEBNISDATEI.parent.mkdir(exist_ok=True)
    neu = not ERGEBNISDATEI.exists()
    with open(ERGEBNISDATEI, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SPALTEN)
        if neu:
            w.writeheader()
        w.writerow(zeile)

    print(json.dumps(zeile, indent=2))
    if anzahl and fehler / max(anzahl + fehler, 1) > 0.01:
        print("ACHTUNG: mehr als 1 % Fehler, Lauf wiederholen.")


if __name__ == "__main__":
    main()