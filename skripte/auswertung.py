"""
Auswertung der Messreihe fuer Kapitel 4 der Arbeit.

Liest ergebnisse/messungen.csv und die beiden Dateien zum Speicherbedarf und
schreibt nach ergebnisse/auswertung/: Tabellen (CSV und Markdown), Abbildungen
(PNG mit 300 dpi und PDF) und den Bericht bericht.md.

Die Messdateien werden nur gelesen. Das Skript enthaelt keinen Zufall und
keine Zeitstempel; ein wiederholter Aufruf liefert dieselben Dateien.

Aufruf:
    .venv/bin/python skripte/auswertung.py
"""

import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                      # ohne Fenster zeichnen
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, NullFormatter

WURZEL = Path(__file__).resolve().parent.parent
MESSDATEI = WURZEL / "ergebnisse" / "messungen.csv"
SPEICHERDATEI = {"sf1": WURZEL / "ergebnisse" / "speicherbedarf_sf1.txt",
                 "sf2": WURZEL / "ergebnisse" / "speicherbedarf_sf2.txt"}
AUSGABE = WURZEL / "ergebnisse" / "auswertung"

# --- Festlegungen aus Abschnitt 3.3.2 der Arbeit -------------------------

ARME = ["A", "B", "C"]
ARM_NAME = {"A": "Arm A (PostgreSQL, normalisiert)",
            "B": "Arm B (MongoDB, referenziert)",
            "C": "Arm C (MongoDB, eingebettet)"}
ERWARTETE_ZEILEN = 150
LAEUFE = [1, 2, 3, 4, 5]
SCHWELLE_PROZENT = 10.0                    # Relevanzschwelle fuer einen Effekt
MASSGEBLICH = "p95_ms"                     # massgebliche Kennzahl fuer Effekte
KENNZAHL_NAME = {"p95_ms": "95. Perzentil", "median_ms": "Median"}
PUFFER_BYTES = 2 ** 30                     # shared_buffers bzw. WiredTiger-Cache: 1 GB
VERGLEICHE = [("Modelleffekt", "B", "C"),
              ("Systemeffekt", "A", "B"),
              ("Gesamtunterschied", "A", "C")]
FANOUTS = [10, 100, 1000]

# --- Gestaltung der Abbildungen ------------------------------------------

# Farben je Arm, in allen Abbildungen gleich. Geprueft auf Unterscheidbarkeit
# bei Rot-Gruen-Sehschwaeche; Schraffur und Marker tragen die Zuordnung
# zusaetzlich, damit sie auch im Graustufendruck erkennbar bleibt.
FARBE = {"A": "#26548F", "B": "#4A97D7", "C": "#C4572F"}
SCHRAFFUR = {"A": "", "B": "///", "C": "\\\\\\"}
MARKER = {"A": "o", "B": "s", "C": "^"}
TEXT, TEXT_SCHWACH, GITTER = "#222222", "#555555", "#DDDDDD"

plt.rcParams.update({
    "font.size": 11, "axes.titlesize": 13, "axes.labelsize": 11,
    "xtick.labelsize": 11, "ytick.labelsize": 10, "legend.fontsize": 10,
    "axes.edgecolor": TEXT_SCHWACH, "axes.labelcolor": TEXT, "text.color": TEXT,
    "xtick.color": TEXT, "ytick.color": TEXT, "hatch.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 100, "savefig.dpi": 300,
})


# --- Hilfsfunktionen fuer Zahlen und Tabellen ----------------------------

def de(wert, stellen=3):
    """Zahl mit Dezimalkomma und Tausenderpunkt."""
    text = f"{wert:,.{stellen}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def de_kurz(wert):
    """Fuer Beschriftungen: so viele Stellen, wie die Groessenordnung braucht."""
    if wert >= 100:
        return de(wert, 0)
    if wert >= 10:
        return de(wert, 1)
    if wert >= 1:
        return de(wert, 2)
    return de(wert, 3)


def markdown_tabelle(df):
    """DataFrame als Markdown-Tabelle (alle Werte sind bereits Text)."""
    zeilen = ["| " + " | ".join(df.columns) + " |",
              "|" + "|".join(["---"] * len(df.columns)) + "|"]
    for _, z in df.iterrows():
        zeilen.append("| " + " | ".join(str(w) for w in z) + " |")
    return "\n".join(zeilen)


def schreibe_tabelle(name, roh, anzeige):
    """Rohwerte als CSV, lesbare Fassung als Markdown. Liefert den Markdown-Text."""
    roh.to_csv(AUSGABE / f"{name}.csv", index=False, lineterminator="\n")
    text = markdown_tabelle(anzeige)
    (AUSGABE / f"{name}.md").write_text(text + "\n", encoding="utf-8")
    return text


def kombi_name(profil, sf, fanout):
    text = f"{profil} {sf.upper()}"
    if profil == "L3":
        text += f" Fan-out {fanout}"
    return text


# --- 1. Einlesen, Pruefen, Verdichten ------------------------------------

def lies_messungen():
    """Messdatei lesen und die Vorbedingungen pruefen. Abbruch bei Verstoss."""
    df = pd.read_csv(MESSDATEI)
    probleme = []
    if len(df) != ERWARTETE_ZEILEN:
        probleme.append(f"{len(df)} Zeilen statt {ERWARTETE_ZEILEN}")
    je_kombi = df.groupby(["sf", "profil", "fanout", "arm"])["lauf"].apply(sorted)
    for schluessel, laeufe in je_kombi.items():
        if laeufe != LAEUFE:
            probleme.append(f"Kombination {schluessel}: Laeufe {laeufe} statt {LAEUFE}")
    if len(je_kombi) != ERWARTETE_ZEILEN // len(LAEUFE):
        probleme.append(f"{len(je_kombi)} Kombinationen statt "
                        f"{ERWARTETE_ZEILEN // len(LAEUFE)}")
    if (df["fehler"] != 0).any():
        probleme.append(f"{int((df['fehler'] != 0).sum())} Zeilen mit Fehlern")
    if (df["operationen"] <= 0).any():
        probleme.append("Zeilen ohne Operationen")
    if probleme:
        raise SystemExit("ABBRUCH, Messdatei erfuellt die Vorbedingungen nicht:\n  "
                         + "\n  ".join(probleme))
    return df


def verdichte(df):
    """
    Je Kombination: Median ueber die fuenf Laeufe sowie kleinster und groesster
    Wert, fuer Median-Antwortzeit, 95. Perzentil und Durchsatz.
    """
    gruppen = df.groupby(["sf", "profil", "fanout", "arm"])
    v = gruppen.agg(
        median_ms=("median_ms", "median"), median_ms_min=("median_ms", "min"),
        median_ms_max=("median_ms", "max"),
        p95_ms=("p95_ms", "median"), p95_ms_min=("p95_ms", "min"),
        p95_ms_max=("p95_ms", "max"),
        durchsatz_ops=("durchsatz_ops", "median"),
        durchsatz_ops_min=("durchsatz_ops", "min"),
        durchsatz_ops_max=("durchsatz_ops", "max"),
        geschrieben=("geschrieben_schnitt", "median"),
        clients=("clients", "first"), dauer_s=("dauer_s", "median"),
    )
    return v


def tabelle_verdichtung(v):
    roh = v.reset_index()
    reihenfolge = {"L1": 0, "L2": 1, "L3": 2, "L4-Q1": 3, "L4-Q2": 4}
    roh = roh.sort_values(by=["profil", "sf", "fanout", "arm"],
                          key=lambda s: s.map(reihenfolge) if s.name == "profil" else s)
    roh = roh.drop(columns=["clients", "dauer_s"]).reset_index(drop=True)
    anzeige = pd.DataFrame({
        "Profil": roh["profil"], "Datenmenge": roh["sf"].str.upper(),
        "Fan-out": [str(f) if p == "L3" else "–" for f, p in zip(roh["fanout"], roh["profil"])],
        "Arm": roh["arm"],
        "Median (ms)": [de(w) for w in roh["median_ms"]],
        "Spannweite Median (ms)": [f"{de(a)} – {de(b)}" for a, b in
                                   zip(roh["median_ms_min"], roh["median_ms_max"])],
        "95. Perzentil (ms)": [de(w) for w in roh["p95_ms"]],
        "Spannweite 95. Perzentil (ms)": [f"{de(a)} – {de(b)}" for a, b in
                                          zip(roh["p95_ms_min"], roh["p95_ms_max"])],
        "Durchsatz (Ops/s)": [de(w, 1) for w in roh["durchsatz_ops"]],
        "Spannweite Durchsatz (Ops/s)": [f"{de(a, 1)} – {de(b, 1)}" for a, b in
                                         zip(roh["durchsatz_ops_min"], roh["durchsatz_ops_max"])],
        "Geschrieben je Operation": [de(g, 1) if p in ("L2", "L3") else "–"
                                     for g, p in zip(roh["geschrieben"], roh["profil"])],
    })
    return roh, anzeige


# --- 2. Effekte ----------------------------------------------------------

def vergleiche(zeile_x, zeile_y, name_x, name_y, kennzahl):
    """
    Vergleicht zwei verdichtete Zeilen in einer Kennzahl.
    Prozent bezieht sich auf den kleineren (schnelleren) Wert. Ein Effekt liegt
    vor, wenn der Unterschied mindestens 10 % betraegt und sich die Spannweiten
    der fuenf Laeufe nicht ueberschneiden.
    """
    x, y = zeile_x[kennzahl], zeile_y[kennzahl]
    klein, gross = min(x, y), max(x, y)
    faktor = gross / klein
    prozent = (faktor - 1) * 100
    getrennt = (zeile_x[f"{kennzahl}_max"] < zeile_y[f"{kennzahl}_min"]
                or zeile_y[f"{kennzahl}_max"] < zeile_x[f"{kennzahl}_min"])
    if x == y:
        schneller = "gleich"
    else:
        schneller = name_x if x < y else name_y
    return {"wert_x": x, "wert_y": y, "schneller": schneller, "faktor": faktor,
            "prozent": prozent, "spannweiten_getrennt": getrennt,
            "effekt": prozent >= SCHWELLE_PROZENT and getrennt}


def urteil_text(e):
    if e["effekt"]:
        return f"Effekt, {e['schneller']} schneller"
    grund = []
    if e["prozent"] < SCHWELLE_PROZENT:
        grund.append("unter 10 %")
    if not e["spannweiten_getrennt"]:
        grund.append("Spannweiten überschneiden sich")
    return "kein Effekt (" + ", ".join(grund) + ")"


def weicht_ab(e_p95, e_median):
    """Fuehren 95. Perzentil und Median zu unterschiedlichen Urteilen?"""
    if e_p95["effekt"] != e_median["effekt"]:
        return True
    return e_p95["effekt"] and e_p95["schneller"] != e_median["schneller"]


def tabelle_effekte(v):
    zeilen_roh, zeilen_anzeige = [], []
    kombis = sorted({(p, sf, f) for sf, p, f, _ in v.index},
                    key=lambda k: (k[0], k[1], k[2]))
    for profil, sf, fanout in kombis:
        for bezeichnung, x, y in VERGLEICHE:
            zx, zy = v.loc[(sf, profil, fanout, x)], v.loc[(sf, profil, fanout, y)]
            e95 = vergleiche(zx, zy, x, y, "p95_ms")
            emed = vergleiche(zx, zy, x, y, "median_ms")
            abweichung = weicht_ab(e95, emed)
            zeilen_roh.append({
                "profil": profil, "sf": sf, "fanout": fanout,
                "vergleich": bezeichnung, "arm_x": x, "arm_y": y,
                "p95_x_ms": e95["wert_x"], "p95_y_ms": e95["wert_y"],
                "p95_schneller": e95["schneller"], "p95_faktor": round(e95["faktor"], 4),
                "p95_prozent": round(e95["prozent"], 2),
                "p95_spannweiten_getrennt": e95["spannweiten_getrennt"],
                "p95_effekt": e95["effekt"],
                "median_x_ms": emed["wert_x"], "median_y_ms": emed["wert_y"],
                "median_schneller": emed["schneller"],
                "median_faktor": round(emed["faktor"], 4),
                "median_prozent": round(emed["prozent"], 2),
                "median_spannweiten_getrennt": emed["spannweiten_getrennt"],
                "median_effekt": emed["effekt"],
                "urteile_weichen_ab": abweichung})
            zeilen_anzeige.append({
                "Kombination": kombi_name(profil, sf, fanout),
                "Vergleich": f"{bezeichnung}: {x} gegen {y}",
                "95. Perzentil (ms)": f"{x} {de(e95['wert_x'])} / {y} {de(e95['wert_y'])}",
                "Faktor": de(e95["faktor"], 2), "Prozent": de(e95["prozent"], 1),
                "Urteil (95. Perzentil, maßgeblich)": urteil_text(e95),
                "Median (ms)": f"{x} {de(emed['wert_x'])} / {y} {de(emed['wert_y'])}",
                "Faktor Median": de(emed["faktor"], 2),
                "Urteil (Median)": urteil_text(emed),
                "Urteile weichen ab": "JA" if abweichung else "nein"})
    anzeige = pd.DataFrame(zeilen_anzeige)
    return pd.DataFrame(zeilen_roh), anzeige


# --- 3. Hypothesen -------------------------------------------------------

def pruefe_h1(v, kennzahl):
    """
    H1 je Datenmenge. Teilbedingungen:
      L1: C schneller als B, als Effekt.
      L4-Q1 und L4-Q2: C nicht schneller als B, d. h. kein Effekt zugunsten von C.
    """
    zeilen = []
    for sf in ("sf1", "sf2"):
        e = vergleiche(v.loc[(sf, "L1", 0, "B")], v.loc[(sf, "L1", 0, "C")], "B", "C", kennzahl)
        erfuellt_l1 = e["effekt"] and e["schneller"] == "C"
        zeilen.append({"hypothese": "H1", "sf": sf, "kennzahl": kennzahl,
                       "teilbedingung": "L1: C schneller als B (Effekt)",
                       "wert_b_ms": e["wert_x"], "wert_c_ms": e["wert_y"],
                       "faktor": round(e["faktor"], 4), "befund": urteil_text(e),
                       "erfuellt": erfuellt_l1})
        alle = [erfuellt_l1]
        for abfrage in ("Q1", "Q2"):
            e = vergleiche(v.loc[(sf, f"L4-{abfrage}", 0, "B")],
                           v.loc[(sf, f"L4-{abfrage}", 0, "C")], "B", "C", kennzahl)
            erfuellt = not (e["effekt"] and e["schneller"] == "C")
            alle.append(erfuellt)
            zeilen.append({"hypothese": "H1", "sf": sf, "kennzahl": kennzahl,
                           "teilbedingung": f"L4-{abfrage}: C nicht schneller als B",
                           "wert_b_ms": e["wert_x"], "wert_c_ms": e["wert_y"],
                           "faktor": round(e["faktor"], 4), "befund": urteil_text(e),
                           "erfuellt": erfuellt})
        zeilen.append({"hypothese": "H1", "sf": sf, "kennzahl": kennzahl,
                       "teilbedingung": "Gesamturteil (alle Teilbedingungen)",
                       "wert_b_ms": None, "wert_c_ms": None, "faktor": None,
                       "befund": "bestätigt" if all(alle) else "nicht bestätigt",
                       "erfuellt": all(alle)})
    return zeilen


def pruefe_h2(v, kennzahl):
    """
    H2 auf SF1. Teilbedingungen:
      C: Antwortzeit steigt von 10 zu 100 und von 100 zu 1000 jeweils um einen Effekt.
      A und B: groesster und kleinster Wert der drei Stufen liegen innerhalb von 10 %.
    """
    zeilen, alle = [], []
    for von, bis in ((10, 100), (100, 1000)):
        e = vergleiche(v.loc[("sf1", "L3", von, "C")], v.loc[("sf1", "L3", bis, "C")],
                       f"Fan-out {von}", f"Fan-out {bis}", kennzahl)
        erfuellt = e["effekt"] and e["wert_y"] > e["wert_x"]
        alle.append(erfuellt)
        zeilen.append({"hypothese": "H2", "sf": "sf1", "kennzahl": kennzahl,
                       "teilbedingung": f"C: Anstieg von Fan-out {von} zu {bis} (Effekt)",
                       "werte_ms": f"{de(e['wert_x'])} → {de(e['wert_y'])}",
                       "kennwert": f"Faktor {de(e['faktor'], 2)}",
                       "befund": urteil_text(e) if not erfuellt else "Effekt, Anstieg",
                       "erfuellt": erfuellt})
    for arm in ("A", "B"):
        werte = [v.loc[("sf1", "L3", f, arm)][kennzahl] for f in FANOUTS]
        streuung = (max(werte) / min(werte) - 1) * 100
        erfuellt = streuung < SCHWELLE_PROZENT
        alle.append(erfuellt)
        zeilen.append({"hypothese": "H2", "sf": "sf1", "kennzahl": kennzahl,
                       "teilbedingung": f"{arm}: über alle drei Stufen innerhalb von 10 %",
                       "werte_ms": " / ".join(de(w) for w in werte),
                       "kennwert": f"Abstand {de(streuung, 1)} %",
                       "befund": "innerhalb von 10 %" if erfuellt else "außerhalb von 10 %",
                       "erfuellt": erfuellt})
    zeilen.append({"hypothese": "H2", "sf": "sf1", "kennzahl": kennzahl,
                   "teilbedingung": "Gesamturteil (alle Teilbedingungen)",
                   "werte_ms": "", "kennwert": "",
                   "befund": "bestätigt" if all(alle) else "nicht bestätigt",
                   "erfuellt": all(alle)})
    return zeilen


def tabellen_hypothesen(v):
    h1 = pd.DataFrame(pruefe_h1(v, "p95_ms") + pruefe_h1(v, "median_ms"))
    h2 = pd.DataFrame(pruefe_h2(v, "p95_ms") + pruefe_h2(v, "median_ms"))

    def ja_nein(w):
        return "ja" if w else "nein"

    h1_anzeige = pd.DataFrame({
        "Kennzahl": [KENNZAHL_NAME[k] + (" (maßgeblich)" if k == MASSGEBLICH else "")
                     for k in h1["kennzahl"]],
        "Datenmenge": h1["sf"].str.upper(), "Teilbedingung": h1["teilbedingung"],
        "B (ms)": [de(w) if pd.notna(w) else "" for w in h1["wert_b_ms"]],
        "C (ms)": [de(w) if pd.notna(w) else "" for w in h1["wert_c_ms"]],
        "Befund": h1["befund"], "erfüllt": [ja_nein(w) for w in h1["erfuellt"]]})
    h2_anzeige = pd.DataFrame({
        "Kennzahl": [KENNZAHL_NAME[k] + (" (maßgeblich)" if k == MASSGEBLICH else "")
                     for k in h2["kennzahl"]],
        "Teilbedingung": h2["teilbedingung"], "Werte (ms)": h2["werte_ms"],
        "Kennwert": h2["kennwert"], "Befund": h2["befund"],
        "erfüllt": [ja_nein(w) for w in h2["erfuellt"]]})
    return h1, h1_anzeige, h2, h2_anzeige


# --- 4. Schwellenwert ----------------------------------------------------

def tabelle_schwellenwert(v):
    """
    Mindestzahl Lesezugriffe je Aenderung
      = (Aenderungszeit C - Aenderungszeit X) / (Lesezeit X - Lesezeit C)
    mit den Medianen aus L3 und L1. X ist Arm B bzw. Arm A.
    """
    zeilen = []
    for lese_sf, gemischt in (("sf1", False), ("sf2", True)):
        for gegen in ("B", "A"):
            lesen_x = v.loc[(lese_sf, "L1", 0, gegen)]["median_ms"]
            lesen_c = v.loc[(lese_sf, "L1", 0, "C")]["median_ms"]
            nenner = lesen_x - lesen_c
            for fanout in FANOUTS:
                aendern_x = v.loc[("sf1", "L3", fanout, gegen)]["median_ms"]
                aendern_c = v.loc[("sf1", "L3", fanout, "C")]["median_ms"]
                zaehler = aendern_c - aendern_x
                if nenner <= 0:
                    wert, text = None, (
                        f"kein Schwellenwert: C liest nicht schneller als {gegen} "
                        f"(Lesezeit {gegen} {de(lesen_x)} ms, C {de(lesen_c)} ms)")
                elif zaehler <= 0:
                    wert, text = 0.0, (
                        f"0: C ändert nicht langsamer als {gegen}, Einbettung ist "
                        f"bei jedem Verhältnis im Vorteil")
                else:
                    wert = zaehler / nenner
                    text = de(wert, 2)
                zeilen.append({
                    "gegen_arm": gegen, "fanout": fanout,
                    "lesezeiten_aus": lese_sf, "gemischt": gemischt,
                    "aendern_c_ms": aendern_c, "aendern_x_ms": aendern_x,
                    "lesen_x_ms": lesen_x, "lesen_c_ms": lesen_c,
                    "zaehler_ms": round(zaehler, 6), "nenner_ms": round(nenner, 6),
                    "lesezugriffe_je_aenderung": None if wert is None else round(wert, 4),
                    "ergebnis": text})
    roh = pd.DataFrame(zeilen)
    anzeige = pd.DataFrame({
        "Lesezeiten aus": [f"{s.upper()} (gemischt mit L3 aus SF1)" if g else s.upper()
                           for s, g in zip(roh["lesezeiten_aus"], roh["gemischt"])],
        "C gegen": roh["gegen_arm"], "Fan-out": roh["fanout"],
        "Änderungszeit C (ms)": [de(w) for w in roh["aendern_c_ms"]],
        "Änderungszeit Vergleichsarm (ms)": [de(w) for w in roh["aendern_x_ms"]],
        "Lesezeit Vergleichsarm (ms)": [de(w) for w in roh["lesen_x_ms"]],
        "Lesezeit C (ms)": [de(w) for w in roh["lesen_c_ms"]],
        "Mindestzahl Lesezugriffe je Änderung": roh["ergebnis"]})
    return roh, anzeige


# --- 5. Speicherbedarf ---------------------------------------------------

def lies_speicher(pfad):
    """Liest die Kennzahlen in Bytes aus einer Datei speicherbedarf_*.txt."""
    werte = {"A": {}, "B": {}, "C": {}}
    aktuell = None
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("arm_b ("):
            aktuell = "B"
        elif zeile.startswith("arm_c ("):
            aktuell = "C"
        elif zeile.startswith("  je Collection") or zeile.startswith("==="):
            aktuell = None
        treffer = re.match(r"\s+(?:Summe )?(pg_\w+)\s.*?([\d.]+) Bytes", zeile)
        if treffer:
            werte["A"][treffer.group(1)] = int(treffer.group(2).replace(".", ""))
        treffer = re.match(r"\s+(dataSize|storageSize|indexSize|totalSize)\s.*?([\d.]+) Bytes", zeile)
        if treffer and aktuell:
            werte[aktuell][treffer.group(1)] = int(treffer.group(2).replace(".", ""))
    noetig = {"A": ["pg_table_size", "pg_indexes_size", "pg_total_relation_size",
                    "pg_database_size"],
              "B": ["dataSize", "storageSize", "indexSize", "totalSize"],
              "C": ["dataSize", "storageSize", "indexSize", "totalSize"]}
    for arm, namen in noetig.items():
        fehlend = [n for n in namen if n not in werte[arm]]
        if fehlend:
            raise SystemExit(f"ABBRUCH: {pfad.name} enthaelt fuer Arm {arm} nicht {fehlend}")
    return werte


def tabelle_speicher():
    zeilen = []
    for sf, pfad in SPEICHERDATEI.items():
        w = lies_speicher(pfad)
        a = w["A"]
        zeilen.append({"sf": sf, "arm": "A",
                       "daten_kennzahl": "pg_table_size", "daten_bytes": a["pg_table_size"],
                       "unkomprimiert_kennzahl": "", "unkomprimiert_bytes": None,
                       "indizes_kennzahl": "pg_indexes_size",
                       "indizes_bytes": a["pg_indexes_size"],
                       "gesamt_kennzahl": "pg_total_relation_size",
                       "gesamt_bytes": a["pg_total_relation_size"],
                       "datenbank_bytes": a["pg_database_size"]})
        for arm in ("B", "C"):
            m = w[arm]
            zeilen.append({"sf": sf, "arm": arm,
                           "daten_kennzahl": "storageSize", "daten_bytes": m["storageSize"],
                           "unkomprimiert_kennzahl": "dataSize",
                           "unkomprimiert_bytes": m["dataSize"],
                           "indizes_kennzahl": "indexSize", "indizes_bytes": m["indexSize"],
                           "gesamt_kennzahl": "totalSize", "gesamt_bytes": m["totalSize"],
                           "datenbank_bytes": None})
    roh = pd.DataFrame(zeilen)
    roh["verhaeltnis_zum_puffer"] = (roh["gesamt_bytes"] / PUFFER_BYTES).round(3)

    def mb(b):
        return de(b / 1e6, 1)

    anzeige = pd.DataFrame({
        "Datenmenge": roh["sf"].str.upper(), "Arm": roh["arm"],
        "Daten auf der Platte (MB)": [f"{mb(b)} ({k})" for b, k in
                                      zip(roh["daten_bytes"], roh["daten_kennzahl"])],
        "Daten unkomprimiert (MB)": [f"{mb(b)} (dataSize)" if pd.notna(b)
                                     else "– (keine Entsprechung)"
                                     for b in roh["unkomprimiert_bytes"]],
        "Indizes (MB)": [f"{mb(b)} ({k})" for b, k in
                         zip(roh["indizes_bytes"], roh["indizes_kennzahl"])],
        "Gesamtbedarf Daten + Indizes (MB)": [f"{mb(b)} ({k})" for b, k in
                                              zip(roh["gesamt_bytes"], roh["gesamt_kennzahl"])],
        "Verhältnis zum Puffer (1 GB)": [de(w, 2) for w in roh["verhaeltnis_zum_puffer"]],
        "pg_database_size (MB)": [mb(b) if pd.notna(b) else "–"
                                  for b in roh["datenbank_bytes"]]})
    return roh, anzeige


# --- 6. Abbildungen ------------------------------------------------------

def achsenzahl_log(wert, _):
    """Beschriftung einer logarithmischen Achse: 0,1 / 1 / 10 / 1.000."""
    return de(wert, 0) if wert >= 1 else f"{wert:g}".replace(".", ",")


def achsenzahl_linear(ax):
    """Alle Teilstriche einer linearen Achse mit derselben Stellenzahl beschriften."""
    oben = ax.get_ylim()[1]
    stellen = 2 if oben < 10 else 1 if oben < 100 else 0
    ax.yaxis.set_major_formatter(FuncFormatter(lambda w, _: de(w, stellen)))


def achse_vorbereiten(ax, y_log=False):
    ax.grid(axis="y", color=GITTER, linewidth=0.8)
    ax.set_axisbelow(True)
    if y_log:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(achsenzahl_log))
        ax.yaxis.set_minor_formatter(NullFormatter())


def titel(fig, ax, haupt, unter):
    """Haupttitel gross, Erlaeuterung der Kennzahl kleiner darunter."""
    fig.suptitle(haupt, fontsize=13, x=0.5, y=0.99)
    ax.set_title(unter, fontsize=9.5, color=TEXT_SCHWACH, pad=8)


def legende_arme(ax, **kw):
    griffe = [Patch(facecolor=FARBE[a], hatch=SCHRAFFUR[a], edgecolor="white",
                    label=ARM_NAME[a]) for a in ARME]
    ax.legend(handles=griffe, frameon=False, **kw)


def gruppierte_balken(ax, v, gruppen, kennzahl, y_log=False):
    """
    Zeichnet je Gruppe drei Balken (A, B, C): Median der fuenf Laeufe als Hoehe,
    kleinster und groesster Lauf als Fehlerbalken, Wert als Beschriftung.
    gruppen: Liste aus (Beschriftung, sf, profil, fanout).
    """
    breite = 0.26
    for i, (_, sf, profil, fanout) in enumerate(gruppen):
        for j, arm in enumerate(ARME):
            z = v.loc[(sf, profil, fanout, arm)]
            wert, unten, oben = z[kennzahl], z[f"{kennzahl}_min"], z[f"{kennzahl}_max"]
            x = i + (j - 1) * (breite + 0.02)
            ax.bar(x, wert, breite, color=FARBE[arm], hatch=SCHRAFFUR[arm],
                   edgecolor="white", linewidth=1.0)
            ax.errorbar(x, wert, yerr=[[wert - unten], [oben - wert]], fmt="none",
                        ecolor=TEXT, elinewidth=1.0, capsize=3)
            hoehe = oben * 1.08 if y_log else oben
            ax.annotate(de_kurz(wert), (x, hoehe), xytext=(0, 0 if y_log else 3),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=9, color=TEXT)
    ax.set_xticks(range(len(gruppen)))
    ax.set_xticklabels([g[0] for g in gruppen])
    ax.tick_params(axis="x", length=0)
    unten, oben = ax.get_ylim()
    ax.set_ylim(unten, oben * (1.6 if y_log else 1.10))      # Platz fuer die Beschriftung
    if not y_log:
        achsenzahl_linear(ax)


def speichere(fig, name):
    fig.savefig(AUSGABE / f"{name}.png", dpi=300, bbox_inches="tight",
                metadata={"Software": None})
    # ohne Erstellungsdatum, damit die Datei bei jedem Aufruf gleich ist
    fig.savefig(AUSGABE / f"{name}.pdf", bbox_inches="tight",
                metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)
    return f"{name}.png"


def untertitel(kennzahl):
    return (f"{KENNZAHL_NAME[kennzahl]} der Antwortzeit: Median der fünf Läufe, "
            f"Fehlerbalken: kleinster und größter Lauf")


def abbildung_l1(v, kennzahl):
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    achse_vorbereiten(ax)
    gruppierte_balken(ax, v, [("SF1 (300.000 Aufträge)", "sf1", "L1", 0),
                              ("SF2 (3.500.000 Aufträge)", "sf2", "L1", 0)], kennzahl)
    ax.set_ylabel("Antwortzeit in ms")
    ax.set_xlabel("Datenmenge")
    titel(fig, ax, "L1: Vollständigen Auftrag lesen", untertitel(kennzahl))
    legende_arme(ax, loc="upper left")
    return speichere(fig, f"abb_l1_{kennzahl.replace('_ms', '')}")


def abbildung_l2(v, kennzahl):
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    achse_vorbereiten(ax)
    gruppierte_balken(ax, v, [("SF1 (300.000 Aufträge)", "sf1", "L2", 0)], kennzahl)
    ax.set_xlim(-0.75, 1.55)                 # Balken schmal halten, Platz fuer die Legende
    ax.set_ylabel("Antwortzeit in ms")
    ax.set_xlabel("Datenmenge")
    titel(fig, ax, "L2: Neuen Auftrag anlegen", untertitel(kennzahl))
    legende_arme(ax, loc="center right", bbox_to_anchor=(1.0, 0.25))
    return speichere(fig, f"abb_l2_{kennzahl.replace('_ms', '')}")


def abbildung_l3(v, kennzahl):
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    achse_vorbereiten(ax, y_log=True)
    ax.set_xscale("log")
    for arm in ARME:
        werte = [v.loc[("sf1", "L3", f, arm)] for f in FANOUTS]
        y = [w[kennzahl] for w in werte]
        unten = [w[kennzahl] - w[f"{kennzahl}_min"] for w in werte]
        oben = [w[f"{kennzahl}_max"] - w[kennzahl] for w in werte]
        ax.errorbar(FANOUTS, y, yerr=[unten, oben], color=FARBE[arm], marker=MARKER[arm],
                    markersize=8, markeredgecolor="white", markeredgewidth=1.5,
                    linewidth=2, capsize=3, ecolor=TEXT, elinewidth=1.0,
                    label=ARM_NAME[arm])
        # Endpunkt direkt beschriften; die uebrigen Werte stehen in der Tabelle
        ax.annotate(f"{arm}: {de_kurz(y[-1])} ms", (FANOUTS[-1], y[-1]), xytext=(9, 0),
                    textcoords="offset points", va="center", fontsize=10, color=TEXT)
    ax.set_xticks(FANOUTS)
    ax.set_xticklabels(["10", "100", "1.000"])
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlim(7, 3500)
    ax.set_xlabel("Fan-out: Aufträge des geänderten Kunden (logarithmisch)")
    ax.set_ylabel("Antwortzeit in ms (logarithmisch)")
    titel(fig, ax, "L3: Kundenanschrift ändern, SF1", untertitel(kennzahl))
    griffe = [Line2D([], [], color=FARBE[a], marker=MARKER[a], markersize=8,
                     markeredgecolor="white", markeredgewidth=1.5, linewidth=2,
                     label=ARM_NAME[a]) for a in ARME]
    ax.legend(handles=griffe, frameon=False, loc="upper left")
    unten, oben = ax.get_ylim()
    ax.set_ylim(unten, oben * 1.8)           # Platz fuer die Legende
    return speichere(fig, f"abb_l3_{kennzahl.replace('_ms', '')}")


def abbildung_l4(v, kennzahl):
    fig, ax = plt.subplots(figsize=(9.0, 4.8))
    achse_vorbereiten(ax, y_log=True)
    gruppierte_balken(ax, v, [("Q1, SF1", "sf1", "L4-Q1", 0), ("Q2, SF1", "sf1", "L4-Q2", 0),
                              ("Q1, SF2", "sf2", "L4-Q1", 0), ("Q2, SF2", "sf2", "L4-Q2", 0)],
                      kennzahl, y_log=True)
    ax.set_ylabel("Antwortzeit in ms (logarithmisch)")
    ax.set_xlabel("Abfrage und Datenmenge (Q1: Umsatz je Produkt, Q2: Auftragsliste eines Monats)")
    titel(fig, ax, "L4: Auswertungen über viele Aufträge", untertitel(kennzahl))
    legende_arme(ax, loc="upper left")
    return speichere(fig, f"abb_l4_{kennzahl.replace('_ms', '')}")


def abbildung_speicher(speicher):
    """Gestapelte Balken: Daten auf der Platte plus Indizes, je Datenmenge ein Feld."""
    fig, achsen = plt.subplots(1, 2, figsize=(9.5, 4.8))
    gib = 2 ** 30
    for ax, sf in zip(achsen, ("sf1", "sf2")):
        achse_vorbereiten(ax)
        teil = speicher[speicher["sf"] == sf].set_index("arm")
        for i, arm in enumerate(ARME):
            daten = teil.loc[arm, "daten_bytes"] / gib
            indizes = teil.loc[arm, "indizes_bytes"] / gib
            ax.bar(i, daten, 0.6, color=FARBE[arm], edgecolor="white", linewidth=1.0)
            ax.bar(i, indizes, 0.6, bottom=daten, color=FARBE[arm], alpha=0.55,
                   hatch="///", edgecolor="white", linewidth=1.0)
            ax.annotate(de(daten + indizes, 2), (i, daten + indizes), xytext=(0, 3),
                        textcoords="offset points", ha="center", va="bottom", fontsize=10)
        ax.axhline(1.0, color=TEXT, linewidth=1.2)
        ax.annotate("Puffer\n1 GB", xy=(1.0, 1.0), xycoords=("axes fraction", "data"),
                    xytext=(5, 0), textcoords="offset points", ha="left", va="center",
                    fontsize=10)
        ax.set_xticks(range(3))
        ax.set_xticklabels([f"Arm {a}" for a in ARME])
        ax.tick_params(axis="x", length=0)
        ax.set_xlim(-0.55, 2.55)
        ax.set_ylim(0, max(1.25, teil["gesamt_bytes"].max() / gib * 1.15))
        achsenzahl_linear(ax)
        ax.set_title(f"{sf.upper()} ({'300.000' if sf == 'sf1' else '3.500.000'} Aufträge)",
                     fontsize=12)
        ax.set_ylabel("Belegter Platz in GiB")
    griffe = [Patch(facecolor="#888888", edgecolor="white", label="Daten auf der Platte "
                    "(A: pg_table_size, B und C: storageSize)"),
              Patch(facecolor="#888888", alpha=0.55, hatch="///", edgecolor="white",
                    label="Indizes (A: pg_indexes_size, B und C: indexSize)")]
    fig.legend(handles=griffe, frameon=False, loc="lower center", ncol=1,
               bbox_to_anchor=(0.5, -0.12))
    fig.suptitle("Speicherbedarf je Arm: Daten und Indizes", fontsize=13)
    fig.tight_layout(w_pad=4)
    return speichere(fig, "abb_speicherbedarf")


# --- Bericht -------------------------------------------------------------

def schreibe_bericht(df, teile, abbildungen):
    v_md, eff_roh, eff_md, h1, h1_md, h2, h2_md, schw_md, sp_md = teile
    zeit = sorted(df["zeitpunkt"])
    abweichend = eff_roh[eff_roh["urteile_weichen_ab"]]

    def gesamturteil(tabelle, hyp, sf=None):
        t = tabelle[(tabelle["kennzahl"] == MASSGEBLICH)
                    & tabelle["teilbedingung"].str.startswith("Gesamturteil")]
        if sf:
            t = t[t["sf"] == sf]
        return t["befund"].iloc[0]

    def abweichungen(tabelle, mit_sf):
        """Teilbedingungen, die 95. Perzentil und Median unterschiedlich beurteilen."""
        spalten = ["sf", "teilbedingung"] if mit_sf else ["teilbedingung"]
        breit = tabelle.pivot_table(index=spalten, columns="kennzahl", values="erfuellt",
                                    aggfunc="first", sort=False)
        texte = []
        for schluessel, zeile in breit.iterrows():
            if bool(zeile["p95_ms"]) != bool(zeile["median_ms"]):
                name = (f"{schluessel[0].upper()}, {schluessel[1]}" if mit_sf else schluessel)
                texte.append(f"- {name}: 95. Perzentil "
                             f"{'erfüllt' if zeile['p95_ms'] else 'nicht erfüllt'}, Median "
                             f"{'erfüllt' if zeile['median_ms'] else 'nicht erfüllt'}")
        return texte

    def abweichung_text(tabelle, mit_sf):
        texte = abweichungen(tabelle, mit_sf)
        kopf = (f"Teilbedingungen, die 95. Perzentil und Median unterschiedlich "
                f"beurteilen: {len(texte)}.")
        return [kopf] + texte

    z = ["# Auswertung der Messreihe",
         "",
         "Erzeugt von `skripte/auswertung.py` aus `ergebnisse/messungen.csv`, "
         "`ergebnisse/speicherbedarf_sf1.txt` und `ergebnisse/speicherbedarf_sf2.txt`. "
         "Der Bericht nennt Messwerte und die Urteile nach den vorab festgelegten "
         "Regeln, keine Deutung.",
         "",
         "## 1. Datengrundlage und Vorprüfung",
         "",
         f"- Zeilen in `messungen.csv`: {len(df)} (Soll {ERWARTETE_ZEILEN}): erfüllt",
         f"- Kombinationen aus Arm, Lastprofil, Datenmenge, Fan-out-Stufe und Abfrage: "
         f"{df.groupby(['sf', 'profil', 'fanout', 'arm']).ngroups}, je Kombination genau "
         f"die Läufe 1 bis 5: erfüllt",
         f"- Zeilen mit Fehlern: {int((df['fehler'] != 0).sum())}: erfüllt",
         f"- Clients je Lauf: {', '.join(str(c) for c in sorted(df['clients'].unique()))}; "
         f"Messdauer je Lauf: {de(df['dauer_s'].min(), 1)} bis {de(df['dauer_s'].max(), 1)} s",
         f"- Zeitraum der Läufe: {zeit[0]} bis {zeit[-1]}",
         "",
         "## 2. Verdichtung je Kombination",
         "",
         "Je Kombination der Median über die fünf Läufe, dazu kleinster und größter "
         "Lauf als Spannweite. Dateien: `tabelle_verdichtung.csv`, `tabelle_verdichtung.md`.",
         "", v_md, "",
         "## 3. Effekte",
         "",
         f"Regel: Ein Unterschied gilt als Effekt, wenn er mindestens "
         f"{de(SCHWELLE_PROZENT, 0)} % beträgt und sich die Spannweiten der fünf Läufe "
         f"nicht überschneiden. Maßgeblich ist das 95. Perzentil; der Median wird "
         f"zusätzlich berichtet. Faktor = größerer Wert geteilt durch kleineren Wert, "
         f"Prozent = Abstand bezogen auf den kleineren (schnelleren) Wert. "
         f"Dateien: `tabelle_effekte.csv`, `tabelle_effekte.md`.",
         "", eff_md, "",
         f"Vergleiche, in denen 95. Perzentil und Median zu unterschiedlichen Urteilen "
         f"führen: {len(abweichend)} von {len(eff_roh)}."]
    for _, a in abweichend.iterrows():
        z.append(f"- {kombi_name(a['profil'], a['sf'], a['fanout'])}, {a['vergleich']} "
                 f"({a['arm_x']} gegen {a['arm_y']}): 95. Perzentil "
                 f"{'Effekt' if a['p95_effekt'] else 'kein Effekt'}"
                 f"{' (' + a['p95_schneller'] + ' schneller)' if a['p95_effekt'] else ''}, "
                 f"Median {'Effekt' if a['median_effekt'] else 'kein Effekt'}"
                 f"{' (' + a['median_schneller'] + ' schneller)' if a['median_effekt'] else ''}")
    z += ["",
          "## 4. Hypothesen",
          "",
          "### H1",
          "",
          "Regel: H1 ist bestätigt, wenn C in L1 schneller ist als B (Effekt) und in L4 "
          "nicht schneller als B. „Nicht schneller“ heißt hier: Es liegt kein Effekt "
          "zugunsten von C vor. Geprüft je Datenmenge und je Abfrage. "
          "Dateien: `tabelle_h1.csv`, `tabelle_h1.md`.",
          "", h1_md, "",
          f"Urteil nach der maßgeblichen Kennzahl (95. Perzentil): "
          f"SF1 {gesamturteil(h1, 'H1', 'sf1')}, SF2 {gesamturteil(h1, 'H1', 'sf2')}.",
          "", *abweichung_text(h1, True), "",
          "### H2",
          "",
          "Regel: H2 ist bestätigt, wenn die Antwortzeit von C in L3 von Fan-out 10 zu "
          "100 und von 100 zu 1000 jeweils um einen Effekt steigt, während A und B über "
          "alle drei Stufen innerhalb von 10 % bleiben (größter gegen kleinsten Wert der "
          "drei Stufen). Dateien: `tabelle_h2.csv`, `tabelle_h2.md`.",
          "", h2_md, "",
          f"Urteil nach der maßgeblichen Kennzahl (95. Perzentil): {gesamturteil(h2, 'H2')}.",
          "", *abweichung_text(h2, False), "",
          "## 5. Schwellenwert",
          "",
          "Mindestzahl Lesezugriffe je Änderung = (Änderungszeit C − Änderungszeit "
          "Vergleichsarm) / (Lesezeit Vergleichsarm − Lesezeit C), mit den Medianen aus "
          "L3 (SF1) und L1. Liegt das Verhältnis von Lese- zu Änderungszugriffen einer "
          "Anwendung über dem Wert, ist die Einbettung im Vorteil. Die Zeilen mit "
          "Lesezeiten aus SF2 sind gemischt: L3 wurde nur auf SF1 gemessen. "
          "Dateien: `tabelle_schwellenwert.csv`, `tabelle_schwellenwert.md`.",
          "", schw_md, "",
          "## 6. Speicherbedarf",
          "",
          "Die Kennzahlen sind je System verschieden. PostgreSQL hat keine Entsprechung "
          "zu dataSize und storageSize; vergleichbar über alle drei Arme ist der "
          "Gesamtbedarf aus Daten und Indizes. MB = 10^6 Bytes; das Verhältnis zum "
          "Puffer bezieht den Gesamtbedarf auf 1 GB = 2^30 Bytes. "
          "Dateien: `tabelle_speicher.csv`, `tabelle_speicher.md`.",
          "", sp_md, "",
          "## 7. Abbildungen",
          "",
          "Jede Abbildung liegt als PNG (300 dpi) und als PDF vor. Balken und Punkte "
          "zeigen den Median der fünf Läufe, Fehlerbalken den kleinsten und größten Lauf. "
          "Die Abbildungen zu L1 bis L4 gibt es für das 95. Perzentil (maßgeblich) und "
          "für den Median.",
          ""]
    for titel, datei in abbildungen:
        z += [f"**{titel}** (`{datei}`, `{datei.replace('.png', '.pdf')}`)", "",
              f"![{titel}]({datei})", ""]
    (AUSGABE / "bericht.md").write_text("\n".join(z), encoding="utf-8")


# --- Hauptlauf -----------------------------------------------------------

def main():
    AUSGABE.mkdir(parents=True, exist_ok=True)
    df = lies_messungen()
    v = verdichte(df)

    v_roh, v_anzeige = tabelle_verdichtung(v)
    v_md = schreibe_tabelle("tabelle_verdichtung", v_roh, v_anzeige)
    eff_roh, eff_anzeige = tabelle_effekte(v)
    eff_md = schreibe_tabelle("tabelle_effekte", eff_roh, eff_anzeige)
    h1, h1_anzeige, h2, h2_anzeige = tabellen_hypothesen(v)
    h1_md = schreibe_tabelle("tabelle_h1", h1, h1_anzeige)
    h2_md = schreibe_tabelle("tabelle_h2", h2, h2_anzeige)
    schw_roh, schw_anzeige = tabelle_schwellenwert(v)
    schw_md = schreibe_tabelle("tabelle_schwellenwert", schw_roh, schw_anzeige)
    sp_roh, sp_anzeige = tabelle_speicher()
    sp_md = schreibe_tabelle("tabelle_speicher", sp_roh, sp_anzeige)

    abbildungen = []
    for kennzahl in ("p95_ms", "median_ms"):
        k = KENNZAHL_NAME[kennzahl]
        abbildungen += [
            (f"L1, vollständigen Auftrag lesen ({k})", abbildung_l1(v, kennzahl)),
            (f"L2, neuen Auftrag anlegen ({k})", abbildung_l2(v, kennzahl)),
            (f"L3, Kundenanschrift ändern über die Fan-out-Stufen ({k})",
             abbildung_l3(v, kennzahl)),
            (f"L4, Auswertungen Q1 und Q2 ({k})", abbildung_l4(v, kennzahl))]
    abbildungen.append(("Speicherbedarf je Arm und Datenmenge", abbildung_speicher(sp_roh)))

    schreibe_bericht(df, (v_md, eff_roh, eff_md, h1, h1_md, h2, h2_md, schw_md, sp_md),
                     abbildungen)

    print(f"Vorpruefung bestanden: {len(df)} Zeilen, "
          f"{df.groupby(['sf', 'profil', 'fanout', 'arm']).ngroups} Kombinationen, 0 Fehler.")
    print(f"Ausgabe in {AUSGABE.relative_to(WURZEL)}:")
    for pfad in sorted(AUSGABE.iterdir()):
        print(f"  {pfad.name}")


if __name__ == "__main__":
    sys.exit(main())
