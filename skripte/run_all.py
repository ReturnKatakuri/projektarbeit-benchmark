"""
Arbeitet den Messplan (Abschnitt 3.4 der Arbeit) ohne Eingriff ab.

Block 1: SF1 geladen -> L1, L4 (lesen), danach L2, L3 (schreiben)
Block 2: einmal auf SF2 umladen -> L1, L4

Vor jedem Schreiblauf wird der betroffene Arm aus einem Dump
wiederhergestellt und sein Container neu gestartet. Bei Leseprofilen wird
der Container einmal je Profil und Arm neu gestartet. Bereits vorhandene
Laeufe in ergebnisse/messungen.csv werden uebersprungen, das Skript setzt
also nach einem Abbruch dort fort, wo es aufgehoert hat.

bench.py, load_data.py und mongo_setup.py laufen dabei in einem
Client-Container im Docker-Netz der Datenbanken (skripte/client_container.sh),
nicht auf dem Host. Zuruecksetzen, Dumps und Container-Neustarts laufen vom
Host aus ueber docker exec. Mit --host-client laufen die Skripte wie frueher
auf dem Host; dann braucht das Python des Hosts die Pakete aus .venv.

Aufruf:
    .venv/bin/python skripte/run_all.py --trocken
    .venv/bin/python skripte/run_all.py --probe --ohne-sf2
    .venv/bin/python skripte/run_all.py
"""

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
SKRIPTE = WURZEL / "skripte"
CLIENT_SKRIPT = WURZEL / "skripte" / "client_container.sh"
CLIENT_CONTAINER = "arm_client"          # Name aus client_container.sh
ERGEBNISDATEI = WURZEL / "ergebnisse" / "messungen.csv"
LOGDATEI = WURZEL / "ergebnisse" / "run_all.log"

# --- Messplan -----------------------------------------------------------

ARME = ["A", "B", "C"]
SCHREIBPROFILE = ["L2", "L3"]
# Reihenfolge je Block: erst lesen, dann schreiben (Schreiben veraendert den Bestand)
PROFILE_JE_SF = {"sf1": ["L1", "L4", "L2", "L3"],
                 "sf2": ["L1", "L4"]}
FANOUTS = [10, 100, 1000]
ABFRAGEN = ["Q1", "Q2"]

CLIENTS = 4
AUFWAERMEN, DAUER = 60, 120               # Normalbetrieb
PROBE_AUFWAERMEN, PROBE_DAUER = 10, 20    # --probe
PROBE_LAUFNUMMER = 50
HOST_LAUFNUMMER = 60                      # --host-client: Zeilen bleiben erkennbar
FEHLERGRENZE = 0.01                       # wie in bench.py: ueber 1 % -> wiederholen
L2_ABSTAND = 1_000_000                    # bench.py vergibt neue Auftraege ab Anzahl + 1.000.000

# --- Container, Datenbanken, Dumps --------------------------------------

CONTAINER = {"A": "arm_postgres", "B": "arm_mongodb", "C": "arm_mongodb"}
DIENST = {"A": "postgres", "B": "mongodb", "C": "mongodb"}   # Namen in docker-compose.yml
DATENBANK = {"A": "arm_a", "B": "arm_b", "C": "arm_c"}
PG_DUMP = "/tmp/arm_a.dump"
MONGO_DUMP = "/tmp/dump"
# Marke im Container: zu welcher Datenmenge die Dumps gehoeren
DUMP_MARKE = "/tmp/dump_sf.txt"

TROCKEN = False   # wird in main() aus --trocken gesetzt
HOST_CLIENT = False   # wird in main() aus --host-client gesetzt


class Abbruch(Exception):
    """Fehler, nach dem nicht weiter gemessen werden darf."""


# --- Ausgabe ------------------------------------------------------------

def melde(text=""):
    """Meldung auf den Bildschirm und mit Zeitstempel in run_all.log."""
    zeile = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(zeile, flush=True)
    with open(LOGDATEI, "a", encoding="utf-8") as f:
        f.write(zeile + "\n")


def melde_fehler(text):
    melde("!" * 72)
    melde(f"!!! FEHLER: {text}")
    melde("!" * 72)


def dauer_text(sekunden):
    minuten = int(round(sekunden / 60))
    return f"{minuten // 60} h {minuten % 60:02d} min"


# --- Plan und Fortsetzen ------------------------------------------------

def csv_profil(lauf):
    """So steht das Profil in messungen.csv (L4 mit Abfrage, wie bench.py)."""
    if lauf["profil"] == "L4":
        return f"L4-{lauf['abfrage']}"
    return lauf["profil"]


def schluessel(lauf):
    return (lauf["arm"], csv_profil(lauf), lauf["sf"], lauf["fanout"], lauf["lauf"])


def beschreibung(lauf):
    text = f"{lauf['sf'].upper()} {csv_profil(lauf)} Arm {lauf['arm']}"
    if lauf["profil"] == "L3":
        text += f" Fan-out {lauf['fanout']}"
    return text + f" Lauf {lauf['lauf']}"


def ist_schreiblauf(lauf):
    return lauf["profil"] in SCHREIBPROFILE


def baue_plan(bloecke, profile, laufnummern):
    """Alle Laeufe in Ausfuehrungsreihenfolge: Block, Profil, Arm, Variante, Wiederholung."""
    plan = []
    for sf in bloecke:
        for profil in PROFILE_JE_SF[sf]:
            if profil not in profile:
                continue
            if profil == "L3":
                varianten = [(f, None) for f in FANOUTS]
            elif profil == "L4":
                varianten = [(0, q) for q in ABFRAGEN]
            else:
                varianten = [(0, None)]
            for arm in ARME:
                for fanout, abfrage in varianten:
                    for nr in laufnummern:
                        plan.append({"sf": sf, "profil": profil, "arm": arm,
                                     "fanout": fanout, "abfrage": abfrage, "lauf": nr})
    return plan


def lies_ergebnisse():
    if not ERGEBNISDATEI.exists():
        return []
    with open(ERGEBNISDATEI, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def zeilen_schluessel(zeile):
    return (zeile["arm"], zeile["profil"], zeile["sf"],
            int(zeile["fanout"]), int(zeile["lauf"]))


def fehlerquote(zeile):
    operationen, fehler = int(zeile["operationen"]), int(zeile["fehler"])
    return fehler / max(operationen + fehler, 1)


def zeile_gueltig(zeile, dauer):
    """
    Gibt None zurueck, wenn die Zeile als erledigter Lauf zaehlt,
    sonst den Grund, warum nicht.
    """
    if int(zeile["clients"]) != CLIENTS:
        return f"{zeile['clients']} Clients statt {CLIENTS}"
    if float(zeile["dauer_s"]) < 0.9 * dauer:
        return f"Messdauer {zeile['dauer_s']} s statt {dauer} s"
    if int(zeile["operationen"]) == 0:
        return "keine Operation im Messfenster"
    if fehlerquote(zeile) > FEHLERGRENZE:
        return f"Fehlerquote {fehlerquote(zeile):.1%}"
    return None


def erledigte_laeufe(plan, dauer):
    """Schluessel der Laeufe, die schon gueltig in messungen.csv stehen."""
    im_plan = {schluessel(l) for l in plan}
    erledigt, nicht_gezaehlt = set(), []
    for zeile in lies_ergebnisse():
        try:
            k = zeilen_schluessel(zeile)
        except (KeyError, ValueError):
            continue
        if k not in im_plan:
            continue
        grund = zeile_gueltig(zeile, dauer)
        if grund is None:
            erledigt.add(k)
        else:
            nicht_gezaehlt.append((zeile["zeitpunkt"], k, grund))
    return erledigt, nicht_gezaehlt


# --- Befehle ausfuehren -------------------------------------------------

def befehl(argumente, timeout=900):
    """Fuehrt einen Befehl aus. Scheitert er, wird die Messreihe abgebrochen."""
    try:
        erg = subprocess.run(argumente, cwd=WURZEL, capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Abbruch(f"Zeitueberschreitung nach {timeout} s: {' '.join(argumente)}")
    if erg.returncode != 0:
        raise Abbruch(f"Befehl fehlgeschlagen (Code {erg.returncode}): "
                      f"{' '.join(argumente)}\n{erg.stderr.strip()[-2000:]}")
    return erg.stdout


def psql(sql):
    return befehl(["docker", "exec", "arm_postgres", "psql", "-U", "postgres",
                   "-d", "arm_a", "-tAc", sql]).strip()


def mongo_js(js):
    return befehl(["docker", "exec", "arm_mongodb", "mongosh", "--quiet",
                   "--eval", js]).strip()


def skript_befehl(skript):
    """
    Befehl, der ein Skript aus skripte/ startet. Standard: im Client-Container
    im Docker-Netz. Vom Host aus fiel die Portweiterleitung von Docker Desktop
    unter Dauerlast aus; mit --host-client laeuft das Skript trotzdem dort.
    """
    if HOST_CLIENT:
        return [sys.executable, str(SKRIPTE / skript)]
    return [str(CLIENT_SKRIPT), skript]


def skript_ausfuehren(skript, argumente=()):
    """Skript aus skripte/ starten, Ausgabe fortlaufend ins Log. Liefert die Ausgabe."""
    prozess = subprocess.Popen(skript_befehl(skript) + list(argumente), cwd=WURZEL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
    ausgabe = []
    for zeile in prozess.stdout:
        ausgabe.append(zeile)
        if zeile.strip():
            melde("    | " + zeile.rstrip())
    prozess.wait()
    if prozess.returncode != 0:
        raise Abbruch(f"{skript} endete mit Code {prozess.returncode}")
    return "".join(ausgabe)


# --- Zustand der Datenbanken --------------------------------------------

def lade_kennzahlen(sf):
    pfad = WURZEL / "daten" / sf / "kennzahlen.json"
    if not pfad.exists():
        raise Abbruch(f"{pfad} fehlt; Datenmenge {sf} wurde nicht erzeugt.")
    return json.loads(pfad.read_text(encoding="utf-8"))


def geladene_sf(kandidaten):
    """
    Welche Datenmenge liegt in allen drei Datenbanken? None, wenn keine
    eindeutig passt. Geprueft wird ueber die Auftragsnummern: Eine Datenmenge
    mit N Auftraegen hat genau die Nummern 1..N. Von L2 angelegte Auftraege
    (ab N + 1.000.001) stoeren die Erkennung nicht; die faengt die
    Zustandspruefung ab.
    """
    # groesste Datenmenge zuerst, weil deren Nummernbereich den der kleineren enthaelt
    for sf, kz in sorted(kandidaten.items(), key=lambda e: -e[1]["auftraege"]):
        n = kz["auftraege"]
        a = psql(f"SELECT count(*) FILTER (WHERE auftrag_id <= {n}), "
                 f"count(*) FILTER (WHERE auftrag_id > {n} AND auftrag_id <= {n + L2_ABSTAND}) "
                 f"FROM auftrag")
        bc = mongo_js(
            "const e = [];"
            "for (const d of ['arm_b', 'arm_c']) {"
            "  const x = db.getSiblingDB(d).auftraege;"
            f"  e.push(x.countDocuments({{_id: {{$lte: {n}}}}}) + '|' +"
            f"         x.countDocuments({{_id: {{$gt: {n}, $lte: {n + L2_ABSTAND}}}}}));"
            "}"
            "print(e.join(' '));")
        if all(wert == f"{n}|0" for wert in [a] + bc.split()):
            return sf
    return None


def zustand(kz):
    """
    Prueft, ob der Bestand dem frisch geladenen entspricht.
    Liefert je Arm eine Liste von Abweichungen (leer = sauber).
    Erkennt L2 an den Anzahlen und L3 an den Anschriften: L3 schreibt
    "Neue Strasse ...", die erzeugten Daten enthalten nur "Musterweg ...".
    """
    soll = [kz["kunden"], kz["produkte"], kz["auftraege"], kz["positionen"]]
    namen = ["Kunden", "Produkte", "Auftraege", "Positionen"]

    werte_a = psql(
        "SELECT (SELECT count(*) FROM kunde), (SELECT count(*) FROM produkt),"
        " (SELECT count(*) FROM auftrag), (SELECT count(*) FROM auftragsposition),"
        " (SELECT count(*) FROM kunde WHERE strasse LIKE 'Neue Strasse%')")
    ist = {"A": [int(w) for w in werte_a.split("|")]}

    werte_bc = json.loads(mongo_js("""
        const e = {};
        for (const [arm, d] of [['B', 'arm_b'], ['C', 'arm_c']]) {
          const x = db.getSiblingDB(d);
          let positionen, geaendert = x.kunden.countDocuments({strasse: /^Neue Strasse/});
          if (arm == 'B') {
            positionen = x.positionen.countDocuments({});
          } else {
            const s = x.auftraege.aggregate([{$group: {_id: null,
                        n: {$sum: {$size: '$positionen'}}}}]).toArray();
            positionen = s.length ? s[0].n : 0;
            geaendert += x.auftraege.countDocuments({'kunde.strasse': /^Neue Strasse/});
          }
          e[arm] = [x.kunden.countDocuments({}), x.produkte.countDocuments({}),
                    x.auftraege.countDocuments({}), positionen, geaendert];
        }
        print(JSON.stringify(e));
    """))
    ist.update(werte_bc)

    probleme = {}
    for arm in ARME:
        liste = [f"{name} {w:,} statt {s:,}"
                 for name, w, s in zip(namen, ist[arm][:4], soll) if w != s]
        if ist[arm][4]:
            liste.append(f"{ist[arm][4]} geaenderte Anschriften")
        probleme[arm] = liste
    return probleme


def melde_zustand(probleme):
    for arm in ARME:
        text = "; ".join(probleme[arm]) if probleme[arm] else "sauber"
        melde(f"  Arm {arm}: {text}")


def indizes():
    """Indexnamen aller drei Arme als Text, zum Vergleich vor/nach Restore."""
    a = psql("SELECT string_agg(indexname, ',' ORDER BY indexname) "
             "FROM pg_indexes WHERE schemaname = 'public'")
    bc = mongo_js(
        "for (const d of ['arm_b', 'arm_c']) { const x = db.getSiblingDB(d);"
        "  for (const c of x.getCollectionNames().sort())"
        "    print(d + '.' + c + ': ' + x[c].getIndexes().map(i => i.name).sort().join(',')); }")
    return f"arm_a: {a}\n{bc}"


# --- Dumps, Zuruecksetzen, Neustart, Umladen ----------------------------

def dumps_gueltig(sf):
    """Liegen in beiden Containern Dumps, die zu dieser Datenmenge gehoeren?"""
    for container in ("arm_postgres", "arm_mongodb"):
        erg = subprocess.run(["docker", "exec", container, "cat", DUMP_MARKE],
                             capture_output=True, text=True)
        if erg.returncode != 0 or erg.stdout.strip() != sf:
            return False
    return True


def dumps_verwerfen():
    befehl(["docker", "exec", "arm_postgres", "rm", "-f", PG_DUMP, DUMP_MARKE])
    befehl(["docker", "exec", "arm_mongodb", "rm", "-rf", MONGO_DUMP, DUMP_MARKE])


def dumps_erzeugen(sf):
    """Nur aufrufen, wenn zustand() den Bestand als sauber gemeldet hat."""
    melde("Erzeuge Dumps im Container ...")
    beginn = time.monotonic()
    dumps_verwerfen()
    befehl(["docker", "exec", "arm_postgres", "pg_dump", "-U", "postgres",
            "-Fc", "-d", "arm_a", "-f", PG_DUMP])
    for db in ("arm_b", "arm_c"):
        befehl(["docker", "exec", "arm_mongodb", "mongodump", "--quiet",
                "--db", db, "--out", MONGO_DUMP])
    # Marke erst ganz zum Schluss: Ein halb fertiger Dump gilt so nie als gueltig
    for container in ("arm_postgres", "arm_mongodb"):
        befehl(["docker", "exec", container, "sh", "-c", f"echo {sf} > {DUMP_MARKE}"])
    melde(f"  Dumps fertig in {time.monotonic() - beginn:.1f} s.")


def zuruecksetzen(arm):
    """Stellt einen Arm aus dem Dump wieder her. Liefert die Dauer in s."""
    beginn = time.monotonic()
    if arm == "A":
        befehl(["docker", "exec", "arm_postgres", "pg_restore", "-U", "postgres",
                "--clean", "--if-exists", "-d", "arm_a", PG_DUMP])
        # Ohne ANALYZE fehlen die Statistiken, und Arm A bekaeme schlechte Plaene
        psql("ANALYZE")
    else:
        befehl(["docker", "exec", "arm_mongodb", "mongorestore", "--quiet", "--drop",
                "--nsInclude", f"{DATENBANK[arm]}.*", MONGO_DUMP])
    dauer = time.monotonic() - beginn
    melde(f"  Arm {arm} zurueckgesetzt ({dauer:.1f} s).")
    return dauer


def neustart(arm):
    """Container des Arms neu starten und warten, bis die Datenbank antwortet."""
    dienst = DIENST[arm]
    beginn = time.monotonic()
    befehl(["docker", "compose", "restart", dienst])
    if dienst == "postgres":
        pruefung = ["docker", "exec", "arm_postgres", "psql", "-U", "postgres",
                    "-d", "arm_a", "-tAc", "SELECT 1"]
    else:
        pruefung = ["docker", "exec", "arm_mongodb", "mongosh", "--quiet",
                    "--eval", "db.adminCommand({ping: 1}).ok"]
    while time.monotonic() - beginn < 120:
        if subprocess.run(pruefung, capture_output=True).returncode == 0:
            melde(f"  Container {CONTAINER[arm]} neu gestartet "
                  f"({time.monotonic() - beginn:.1f} s).")
            return
        time.sleep(1)
    raise Abbruch(f"{CONTAINER[arm]} antwortet 120 s nach dem Neustart nicht.")


def umladen(sf, kandidaten):
    """Datenmenge neu in alle drei Datenbanken laden und Indizes anlegen."""
    melde(f"Lade {sf.upper()} in alle drei Datenbanken (load_data.py --drop) ...")
    beginn = time.monotonic()
    dumps_verwerfen()             # alte Dumps gehoeren zum vorherigen Bestand
    ausgabe = skript_ausfuehren("load_data.py", ["--daten", f"daten/{sf}", "--drop"])
    if "!!!" in ausgabe or "ACHTUNG" in ausgabe:
        raise Abbruch("load_data.py meldet Abweichungen, siehe Log.")
    skript_ausfuehren("mongo_setup.py")
    if geladene_sf(kandidaten) != sf:
        raise Abbruch(f"Nach dem Laden ist {sf.upper()} nicht eindeutig erkennbar.")
    melde(f"  {sf.upper()} geladen in {dauer_text(time.monotonic() - beginn)}.")


# --- Block vorbereiten und abschliessen ---------------------------------

def block_vorbereiten(sf, kandidaten, schreiben_offen, zeiten):
    """Sorgt dafuer, dass sf sauber geladen ist und (bei Bedarf) Dumps vorliegen."""
    if TROCKEN:
        melde(f"[trocken] pruefen, ob {sf.upper()} geladen ist, sonst umladen")
        if sf == "sf1":
            melde("[trocken] Bestand pruefen; falls veraendert: aus Dump "
                  "wiederherstellen oder neu laden")
            if schreiben_offen:
                melde("[trocken] falls keine Dumps vorliegen: Dumps erzeugen und "
                      "Wiederherstellen einmal pruefen")
        return

    melde("Pruefe, welche Datenmenge geladen ist ...")
    try:
        ist = geladene_sf(kandidaten)
    except Abbruch:
        ist = None    # z. B. Tabellen fehlen nach "docker compose down --volumes"
    melde(f"  geladen: {ist.upper() if ist else 'unbekannt'}")
    if ist != sf:
        umladen(sf, kandidaten)
    if sf != "sf1":
        return        # SF2 wird nur gelesen, braucht weder Pruefung noch Dumps

    kz = kandidaten[sf]
    melde("Pruefe Bestand (Anzahlen, Anschriften) ...")
    probleme = zustand(kz)
    melde_zustand(probleme)
    veraendert = [arm for arm in ARME if probleme[arm]]
    if veraendert:
        if dumps_gueltig(sf):
            melde(f"Bestand veraendert, stelle Arm {', '.join(veraendert)} aus dem Dump her.")
            for arm in veraendert:
                zuruecksetzen(arm)
        else:
            melde("Bestand veraendert und keine gueltigen Dumps vorhanden: lade neu.")
            umladen(sf, kandidaten)
        probleme = zustand(kz)
        melde_zustand(probleme)
        if any(probleme.values()):
            raise Abbruch("Bestand ist auch nach dem Zuruecksetzen nicht sauber.")

    if schreiben_offen:
        if dumps_gueltig(sf):
            melde("Vorhandene Dumps werden weiterverwendet.")
        else:
            dumps_erzeugen(sf)
            dumps_pruefen(kz, zeiten)


def dumps_pruefen(kz, zeiten):
    """Einmal alle drei Arme wiederherstellen und Anzahlen und Indizes vergleichen."""
    melde("Pruefe das Wiederherstellen aus den Dumps ...")
    vorher = indizes()
    for arm in ARME:
        zeiten.restore[arm].append(zuruecksetzen(arm))
    probleme = zustand(kz)
    melde_zustand(probleme)
    nachher = indizes()
    if nachher != vorher:
        melde("Indizes vorher:\n" + vorher)
        melde("Indizes nachher:\n" + nachher)
        raise Abbruch("Indizes nach dem Wiederherstellen weichen ab.")
    if any(probleme.values()):
        raise Abbruch("Anzahlen nach dem Wiederherstellen weichen ab.")
    melde("  Wiederherstellen funktioniert, Anzahlen und Indizes stimmen.")


def block_abschliessen(sf, kz):
    """Nach Block 1 den Ausgangszustand herstellen, damit SF1 sauber bleibt."""
    if sf != "sf1":
        return
    if TROCKEN:
        melde("[trocken] abschliessend veraenderte Arme aus dem Dump wiederherstellen")
        return
    melde("Abschliessendes Zuruecksetzen von SF1 ...")
    probleme = zustand(kz)
    veraendert = [arm for arm in ARME if probleme[arm]]
    if veraendert and not dumps_gueltig(sf):
        raise Abbruch("SF1 ist veraendert, aber es gibt keine gueltigen Dumps.")
    for arm in veraendert:
        zuruecksetzen(arm)
    probleme = zustand(kz)
    melde_zustand(probleme)
    if any(probleme.values()):
        raise Abbruch("SF1 ist nach dem abschliessenden Zuruecksetzen nicht sauber.")


# --- Einzelner Lauf -----------------------------------------------------

class Zeiten:
    """Erfasst Laufdauern fuer die Restzeitschaetzung."""

    def __init__(self, aufwaermen, dauer):
        self.nominal = aufwaermen + dauer
        self.zusatz = {"lesen": [], "schreiben": []}   # Dauer ueber das Nominal hinaus
        self.restore = {arm: [] for arm in ARME}

    def erfassen(self, lauf, sekunden):
        art = "schreiben" if ist_schreiblauf(lauf) else "lesen"
        self.zusatz[art].append(sekunden - self.nominal)

    def schaetzen(self, laeufe):
        gesamt = 0.0
        for lauf in laeufe:
            art = "schreiben" if ist_schreiblauf(lauf) else "lesen"
            if self.zusatz[art]:
                zusatz = statistics.mean(self.zusatz[art])
            elif art == "schreiben" and self.restore[lauf["arm"]]:
                zusatz = statistics.mean(self.restore[lauf["arm"]]) + 10
            else:
                zusatz = 10 if art == "lesen" else 30   # Startwerte bis zur ersten Messung
            gesamt += self.nominal + zusatz
        return gesamt


def messen(lauf, aufwaermen, dauer):
    """bench.py als Unterprozess. Liefert die neue CSV-Zeile oder None bei Fehler."""
    argumente = skript_befehl("bench.py")
    argumente += ["--arm", lauf["arm"], "--profil", lauf["profil"], "--sf", lauf["sf"],
                  "--lauf", str(lauf["lauf"]), "--clients", str(CLIENTS),
                  "--aufwaermen", str(aufwaermen), "--dauer", str(dauer)]
    if lauf["profil"] == "L3":
        argumente += ["--fanout", str(lauf["fanout"])]
    if lauf["profil"] == "L4":
        argumente += ["--abfrage", lauf["abfrage"]]

    zeilen_vorher = len(lies_ergebnisse())
    try:
        erg = subprocess.run(argumente, cwd=WURZEL, capture_output=True, text=True,
                             timeout=aufwaermen + dauer + 900)
    except subprocess.TimeoutExpired:
        melde_fehler(f"{beschreibung(lauf)}: bench.py haengt, nach Zeitlimit beendet.")
        if not HOST_CLIENT:
            # Der Container laeuft sonst weiter, wenn nur das Startskript endet
            subprocess.run(["docker", "rm", "-f", CLIENT_CONTAINER], capture_output=True)
        return None
    if erg.returncode != 0:
        melde_fehler(f"{beschreibung(lauf)}: bench.py endete mit Code {erg.returncode}")
        for zeile in erg.stderr.strip().splitlines()[-10:]:
            melde("    | " + zeile)
        return None

    for zeile in lies_ergebnisse()[zeilen_vorher:]:
        if zeilen_schluessel(zeile) == schluessel(lauf):
            # Bei Fehlern den Fehlertext von bench.py (stderr) ins Log uebernehmen
            if int(zeile["fehler"]) > 0:
                for text in erg.stderr.strip().splitlines():
                    melde("    | " + text)
            return zeile
    melde_fehler(f"{beschreibung(lauf)}: keine Ergebniszeile in messungen.csv gefunden.")
    return None


def melde_ergebnis(zeile):
    text = (f"  -> Durchsatz {zeile['durchsatz_ops']} Ops/s, "
            f"Median {zeile['median_ms']} ms, p95 {zeile['p95_ms']} ms, "
            f"Operationen {zeile['operationen']}, Fehler {zeile['fehler']} "
            f"({fehlerquote(zeile):.2%})")
    if zeile["profil"] in SCHREIBPROFILE:
        text += f", geschrieben je Op {zeile['geschrieben_schnitt']}"
    melde(text)


def lauf_durchfuehren(lauf, neustart_noetig, aufwaermen, dauer, zeiten):
    """Zuruecksetzen und Neustart nach Regel, dann messen."""
    if TROCKEN:
        if ist_schreiblauf(lauf):
            melde(f"[trocken] Arm {lauf['arm']} aus Dump wiederherstellen, "
                  f"{CONTAINER[lauf['arm']]} neu starten")
        elif neustart_noetig:
            melde(f"[trocken] {CONTAINER[lauf['arm']]} neu starten")
        melde(f"[trocken] bench.py {beschreibung(lauf)}")
        return None
    if ist_schreiblauf(lauf):
        # Vor jedem Schreiblauf: Ausgangszustand und leerer Cache, damit die
        # Fan-out-Stufen nicht voneinander vorgewaermt werden
        zeiten.restore[lauf["arm"]].append(zuruecksetzen(lauf["arm"]))
        neustart(lauf["arm"])
    elif neustart_noetig:
        neustart(lauf["arm"])
    return messen(lauf, aufwaermen, dauer)


# --- Hauptlauf ----------------------------------------------------------

def fremdlast_melden():
    """Hinweis, wenn andere Prozesse viel CPU belegen (stoert die Messung)."""
    try:
        ausgabe = subprocess.run(["ps", "-Ao", "%cpu=,comm="], capture_output=True,
                                 text=True).stdout
    except OSError:
        return
    for zeile in ausgabe.splitlines():
        teile = zeile.split(None, 1)
        if len(teile) == 2 and float(teile[0].replace(",", ".")) >= 30:
            if "Virtualization" in teile[1] or "docker" in teile[1].lower():
                continue
            melde(f"WARNUNG: Fremdlast {teile[0]} % CPU: {Path(teile[1]).name}")


def argumente_lesen():
    p = argparse.ArgumentParser(description="Messplan aus Abschnitt 3.4 abarbeiten.")
    block = p.add_mutually_exclusive_group()
    block.add_argument("--nur-sf1", action="store_true", help="nur Block 1")
    block.add_argument("--nur-sf2", action="store_true", help="nur Block 2")
    block.add_argument("--ohne-sf2", action="store_true",
                       help="Block 2 samt Umladen ueberspringen")
    p.add_argument("--profil", default="L1,L2,L3,L4",
                   help="Auswahl, z. B. L1,L3")
    p.add_argument("--laeufe", type=int, default=None,
                   help="Wiederholungen je Kombination (Standard 5, Probe 1)")
    p.add_argument("--trocken", action="store_true",
                   help="nur ausgeben, was ausgefuehrt wuerde")
    p.add_argument("--probe", action="store_true",
                   help="10 s aufwaermen, 20 s messen, 1 Wiederholung")
    p.add_argument("--lauf-nummer", type=int, default=None,
                   help="erste Laufnummer (Standard 1, Probe 50, --host-client 60)")
    p.add_argument("--host-client", action="store_true",
                   help="bench.py auf dem Host statt im Client-Container starten "
                        "(nur fuer Vergleiche, Laufnummern ab 60)")
    args = p.parse_args()

    args.profile = [x.strip().upper() for x in args.profil.split(",") if x.strip()]
    for profil in args.profile:
        if profil not in ("L1", "L2", "L3", "L4"):
            p.error(f"unbekanntes Profil: {profil}")
    if args.laeufe is None:
        args.laeufe = 1 if args.probe else 5
    if args.lauf_nummer is None:
        if args.host_client:
            args.lauf_nummer = HOST_LAUFNUMMER
        else:
            args.lauf_nummer = PROBE_LAUFNUMMER if args.probe else 1
    args.laufnummern = list(range(args.lauf_nummer, args.lauf_nummer + args.laeufe))
    # Probezeilen duerfen nie die Nummern echter Laeufe tragen und umgekehrt
    if args.probe and args.lauf_nummer < PROBE_LAUFNUMMER:
        p.error(f"im Probemodus Laufnummer ab {PROBE_LAUFNUMMER}")
    # Laeufe vom Host stehen in derselben CSV; die Laufnummer haelt sie von der
    # Messreihe (1 bis 5) und von Probelaeufen im Container (ab 50) getrennt
    if args.host_client and args.lauf_nummer < HOST_LAUFNUMMER:
        p.error(f"mit --host-client Laufnummer ab {HOST_LAUFNUMMER}")
    if (not args.probe and not args.host_client
            and not all(1 <= n <= 5 for n in args.laufnummern)):
        p.error("im Normalbetrieb Laufnummern 1 bis 5")
    return args


def main():
    global TROCKEN, HOST_CLIENT
    args = argumente_lesen()
    TROCKEN = args.trocken
    HOST_CLIENT = args.host_client
    aufwaermen, dauer = ((PROBE_AUFWAERMEN, PROBE_DAUER) if args.probe
                         else (AUFWAERMEN, DAUER))
    if args.nur_sf2:
        bloecke = ["sf2"]
    elif args.nur_sf1 or args.ohne_sf2:
        bloecke = ["sf1"]
    else:
        bloecke = ["sf1", "sf2"]

    LOGDATEI.parent.mkdir(exist_ok=True)
    melde("=" * 72)
    melde(f"run_all.py gestartet: {' '.join(sys.argv[1:]) or '(ohne Parameter)'}")
    melde(f"Modus: {'PROBE' if args.probe else 'Messreihe'}"
          f"{', TROCKEN' if TROCKEN else ''}; {aufwaermen} s aufwaermen, "
          f"{dauer} s messen, {CLIENTS} Clients, Laufnummern {args.laufnummern}")
    melde("Lastgenerator: " + ("auf dem Host (--host-client)" if HOST_CLIENT
                               else f"Client-Container {CLIENT_CONTAINER} im Docker-Netz"))

    if HOST_CLIENT and not TROCKEN:
        try:
            import psycopg, pymongo   # noqa: F401  (nur Pruefung, ob vorhanden)
        except ImportError:
            melde_fehler("psycopg/pymongo fehlen. Mit .venv/bin/python starten.")
            sys.exit(2)

    plan = baue_plan(bloecke, args.profile, args.laufnummern)
    erledigt, nicht_gezaehlt = erledigte_laeufe(plan, dauer)
    offen = [l for l in plan if schluessel(l) not in erledigt]
    melde(f"Plan: {len(plan)} Laeufe, davon {len(plan) - len(offen)} bereits in "
          f"messungen.csv, {len(offen)} offen.")
    for zeitpunkt, k, grund in nicht_gezaehlt:
        melde(f"  nicht gezaehlt ({grund}): {zeitpunkt} {k}")
    if not offen:
        melde("Nichts zu tun.")
        return

    zeiten = Zeiten(aufwaermen, dauer)
    fehlerliste, wiederholungen = [], []
    beginn_gesamt = time.monotonic()
    nummer = 0

    try:
        kandidaten = {}
        if not TROCKEN:
            fremdlast_melden()
            kandidaten = {sf: lade_kennzahlen(sf) for sf in bloecke}

        for sf in bloecke:
            offen_block = [l for l in offen if l["sf"] == sf]
            if not offen_block:
                continue
            melde("-" * 72)
            melde(f"Block {sf.upper()}: {len(offen_block)} offene Laeufe")
            schreiben_offen = any(ist_schreiblauf(l) for l in offen_block)
            block_vorbereiten(sf, kandidaten, schreiben_offen, zeiten)

            letzte_gruppe = None
            for lauf in offen_block:
                nummer += 1
                rest = offen[offen.index(lauf):]
                zusatz = (" zzgl. Umladen auf SF2"
                          if sf == "sf1" and any(l["sf"] == "sf2" for l in rest) else "")
                melde(f"[{nummer}/{len(offen)}] {beschreibung(lauf)} -- "
                      f"Restzeit ca. {dauer_text(zeiten.schaetzen(rest))}{zusatz}")

                # Leseprofile: ein Neustart je Profil und Arm
                gruppe = (lauf["sf"], lauf["profil"], lauf["arm"])
                neustart_noetig = gruppe != letzte_gruppe
                letzte_gruppe = gruppe

                beginn = time.monotonic()
                zeile = lauf_durchfuehren(lauf, neustart_noetig, aufwaermen, dauer, zeiten)
                if TROCKEN:
                    continue
                if zeile is not None:
                    melde_ergebnis(zeile)
                    if fehlerquote(zeile) > FEHLERGRENZE:
                        melde(f"  Mehr als 1 % Fehler: {beschreibung(lauf)} wird einmal "
                              f"wiederholt (beide Zeilen bleiben in messungen.csv).")
                        wiederholungen.append(beschreibung(lauf))
                        zeile = lauf_durchfuehren(lauf, False, aufwaermen, dauer, zeiten)
                        if zeile is not None:
                            melde_ergebnis(zeile)
                zeiten.erfassen(lauf, time.monotonic() - beginn)

                if zeile is None:
                    fehlerliste.append((beschreibung(lauf), "kein Ergebnis"))
                elif int(zeile["operationen"]) == 0:
                    melde_fehler(f"{beschreibung(lauf)}: keine Operation im Messfenster.")
                    fehlerliste.append((beschreibung(lauf), "0 Operationen"))
                elif fehlerquote(zeile) > FEHLERGRENZE:
                    melde_fehler(f"{beschreibung(lauf)}: auch die Wiederholung hat "
                                 f"{fehlerquote(zeile):.1%} Fehler.")
                    fehlerliste.append((beschreibung(lauf),
                                        f"{fehlerquote(zeile):.1%} Fehler"))

            block_abschliessen(sf, kandidaten.get(sf))

    except Abbruch as e:
        melde_fehler(f"ABBRUCH: {e}")
        fehlerliste.append(("Messreihe", f"abgebrochen: {str(e).splitlines()[0]}"))
    except KeyboardInterrupt:
        melde_fehler("ABBRUCH durch Strg+C. Beim naechsten Start wird fortgesetzt.")
        fehlerliste.append(("Messreihe", "abgebrochen durch Strg+C"))

    # --- Zusammenfassung ---
    melde("=" * 72)
    melde(f"Zusammenfassung: {nummer} von {len(offen)} offenen Laeufen bearbeitet "
          f"in {dauer_text(time.monotonic() - beginn_gesamt)}.")
    for arm in ARME:
        if zeiten.restore[arm]:
            melde(f"  Zuruecksetzen Arm {arm}: {len(zeiten.restore[arm])}x, "
                  f"Median {statistics.median(zeiten.restore[arm]):.1f} s")
    for text in wiederholungen:
        melde(f"  wiederholt wegen > 1 % Fehlern: {text}")
    if fehlerliste:
        melde(f"FEHLER ({len(fehlerliste)}):")
        for was, grund in fehlerliste:
            melde(f"  - {was}: {grund}")
        sys.exit(1)
    melde("Keine Fehler.")


if __name__ == "__main__":
    main()
