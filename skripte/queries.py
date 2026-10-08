"""
Die Operationen der vier Lastprofile, je Messarm einmal umgesetzt.

Arm A: PostgreSQL, normalisiert   -> Verbunde, serverseitig
Arm B: MongoDB, referenziert      -> mehrere Abfragen, clientseitig verknuepft
Arm C: MongoDB, eingebettet       -> ein Dokument je Auftrag

Jede Methode liefert eine Zahl zurueck:
  Lesen    -> Anzahl gelesener Positionen bzw. Ergebniszeilen
  Schreiben-> Anzahl geschriebener Zeilen bzw. Dokumente (fuer L3 wichtig)
"""

from datetime import date, timedelta

PG_DBNAME = "arm_a"
MONGO_URL = "mongodb://localhost:27017"


def monatsgrenzen(jahr, monat):
    """Erster Tag des Monats und erster Tag des Folgemonats."""
    von = date(jahr, monat, 1)
    bis = date(jahr + (monat // 12), (monat % 12) + 1, 1)
    return von, bis


# --- Arm A: PostgreSQL, normalisiert -----------------------------------

class ArmA:
    name = "A"

    def __init__(self, host="localhost", port=5432, user="postgres", password="test"):
        import psycopg
        self.conn = psycopg.connect(
            f"host={host} port={port} dbname={PG_DBNAME} user={user} password={password}",
            autocommit=True)

    def close(self):
        self.conn.close()

    # L1: einen Auftrag vollstaendig lesen
    def l1_auftrag_lesen(self, auftrag_id):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT a.auftrag_id, a.bestelldatum,
                       k.name, k.strasse, k.plz, k.ort,
                       ap.positionsnr, p.bezeichnung, ap.menge, ap.einzelpreis
                FROM auftrag a
                JOIN kunde k              ON k.kunde_id = a.kunde_id
                JOIN auftragsposition ap  ON ap.auftrag_id = a.auftrag_id
                JOIN produkt p            ON p.produkt_id = ap.produkt_id
                WHERE a.auftrag_id = %s
            """, (auftrag_id,))
            return len(cur.fetchall())

    # L2: neuen Auftrag anlegen
    def l2_auftrag_anlegen(self, auftrag_id, kunde_id, datum, positionen):
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO auftrag (auftrag_id, kunde_id, bestelldatum) VALUES (%s,%s,%s)",
                    (auftrag_id, kunde_id, datum))
                cur.executemany(
                    "INSERT INTO auftragsposition "
                    "(auftrag_id, positionsnr, produkt_id, menge, einzelpreis) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    [(auftrag_id, nr, pid, menge, preis)
                     for nr, pid, menge, preis in positionen])
        return 1 + len(positionen)

    # L3: Kundenanschrift aendern
    def l3_adresse_aendern(self, kunde_id, strasse, plz, ort):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE kunde SET strasse=%s, plz=%s, ort=%s WHERE kunde_id=%s",
                (strasse, plz, ort, kunde_id))
            return cur.rowcount

    # L4 / Q1: Umsatz je Produkt in einem Monat
    def q1_umsatz_je_produkt(self, jahr, monat):
        von, bis = monatsgrenzen(jahr, monat)
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT ap.produkt_id, SUM(ap.menge * ap.einzelpreis) AS umsatz
                FROM auftrag a
                JOIN auftragsposition ap ON ap.auftrag_id = a.auftrag_id
                WHERE a.bestelldatum >= %s AND a.bestelldatum < %s
                GROUP BY ap.produkt_id
            """, (von, bis))
            return len(cur.fetchall())

    # L4 / Q2: nur Auftragsnummer und Datum eines Monats
    def q2_auftragsliste(self, jahr, monat):
        von, bis = monatsgrenzen(jahr, monat)
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT auftrag_id, bestelldatum FROM auftrag
                WHERE bestelldatum >= %s AND bestelldatum < %s
            """, (von, bis))
            return len(cur.fetchall())


# --- Arm B: MongoDB, referenziert --------------------------------------

class ArmB:
    name = "B"

    def __init__(self, url=MONGO_URL):
        from pymongo import MongoClient
        # journal=True: Schreibvorgaenge werden erst bestaetigt, wenn sie im
        # Journal auf der Platte stehen (wie synchronous_commit=on in Arm A)
        self.client = MongoClient(url, journal=True)
        self.db = self.client["arm_b"]

    def close(self):
        self.client.close()

    def l1_auftrag_lesen(self, auftrag_id):
        # 1. Auftrag, 2. Positionen, 3. Kunde, 4. Produkte
        auftrag = self.db.auftraege.find_one({"_id": auftrag_id})
        if auftrag is None:
            return 0
        positionen = list(self.db.positionen.find({"auftrag_id": auftrag_id}))
        self.db.kunden.find_one({"_id": auftrag["kunde_id"]})
        produkt_ids = sorted({p["produkt_id"] for p in positionen})
        list(self.db.produkte.find({"_id": {"$in": produkt_ids}}))
        return len(positionen)

    def l2_auftrag_anlegen(self, auftrag_id, kunde_id, datum, positionen):
        self.db.auftraege.insert_one(
            {"_id": auftrag_id, "kunde_id": kunde_id, "bestelldatum": datum})
        docs = [{"auftrag_id": auftrag_id, "positionsnr": nr, "produkt_id": pid,
                 "menge": menge, "einzelpreis": preis}
                for nr, pid, menge, preis in positionen]
        if docs:
            self.db.positionen.insert_many(docs)
        return 1 + len(docs)

    def l3_adresse_aendern(self, kunde_id, strasse, plz, ort):
        res = self.db.kunden.update_one(
            {"_id": kunde_id},
            {"$set": {"strasse": strasse, "plz": plz, "ort": ort}})
        return res.modified_count

    def q1_umsatz_je_produkt(self, jahr, monat):
        von, bis = monatsgrenzen(jahr, monat)
        auftrag_ids = [a["_id"] for a in self.db.auftraege.find(
            {"bestelldatum": {"$gte": von.isoformat(), "$lt": bis.isoformat()}},
            {"_id": 1})]
        umsatz = {}
        for i in range(0, len(auftrag_ids), 10000):         # in Bloecken abfragen
            block = auftrag_ids[i:i + 10000]
            # Summe je Produkt auf dem Server bilden; zum Client gehen nur die
            # Teilsummen des Blocks, nicht die einzelnen Positionen
            for p in self.db.positionen.aggregate([
                {"$match": {"auftrag_id": {"$in": block}}},
                {"$group": {"_id": "$produkt_id",
                            "umsatz": {"$sum": {"$multiply": [
                                "$menge", "$einzelpreis"]}}}},
            ]):
                umsatz[p["_id"]] = umsatz.get(p["_id"], 0) + p["umsatz"]
        return len(umsatz)

    def q2_auftragsliste(self, jahr, monat):
        von, bis = monatsgrenzen(jahr, monat)
        return len(list(self.db.auftraege.find(
            {"bestelldatum": {"$gte": von.isoformat(), "$lt": bis.isoformat()}},
            {"_id": 1, "bestelldatum": 1})))


# --- Arm C: MongoDB, eingebettet ---------------------------------------

class ArmC:
    name = "C"

    def __init__(self, url=MONGO_URL):
        from pymongo import MongoClient
        # journal=True: Schreibvorgaenge werden erst bestaetigt, wenn sie im
        # Journal auf der Platte stehen (wie synchronous_commit=on in Arm A)
        self.client = MongoClient(url, journal=True)
        self.db = self.client["arm_c"]

    def close(self):
        self.client.close()

    def l1_auftrag_lesen(self, auftrag_id):
        doc = self.db.auftraege.find_one({"_id": auftrag_id})
        return len(doc["positionen"]) if doc else 0

    def l2_auftrag_anlegen(self, auftrag_id, kunde_id, datum, positionen):
        # Kopien erzeugen: Kunde und Produkte muessen vorher gelesen werden
        kunde = self.db.kunden.find_one({"_id": kunde_id})
        produkt_ids = sorted({pid for _, pid, _, _ in positionen})
        produkte = {p["_id"]: p for p in
                    self.db.produkte.find({"_id": {"$in": produkt_ids}})}
        eingebettet = [{"positionsnr": nr, "produkt_id": pid,
                        "bezeichnung": produkte[pid]["bezeichnung"],
                        "menge": menge, "einzelpreis": preis}
                       for nr, pid, menge, preis in positionen]
        self.db.auftraege.insert_one({
            "_id": auftrag_id, "bestelldatum": datum,
            "kunde": {"kunde_id": kunde_id, "name": kunde["name"],
                      "strasse": kunde["strasse"], "plz": kunde["plz"],
                      "ort": kunde["ort"]},
            "positionen": eingebettet})
        return 1

    def l3_adresse_aendern(self, kunde_id, strasse, plz, ort):
        # Stammdatum plus jede Kopie in den Auftragsdokumenten
        res1 = self.db.kunden.update_one(
            {"_id": kunde_id},
            {"$set": {"strasse": strasse, "plz": plz, "ort": ort}})
        res2 = self.db.auftraege.update_many(
            {"kunde.kunde_id": kunde_id},
            {"$set": {"kunde.strasse": strasse, "kunde.plz": plz,
                      "kunde.ort": ort}})
        return res1.modified_count + res2.modified_count

    def q1_umsatz_je_produkt(self, jahr, monat):
        von, bis = monatsgrenzen(jahr, monat)
        pipeline = [
            {"$match": {"bestelldatum": {"$gte": von.isoformat(),
                                         "$lt": bis.isoformat()}}},
            {"$unwind": "$positionen"},
            {"$group": {"_id": "$positionen.produkt_id",
                        "umsatz": {"$sum": {"$multiply": [
                            "$positionen.menge", "$positionen.einzelpreis"]}}}},
        ]
        return len(list(self.db.auftraege.aggregate(pipeline)))

    def q2_auftragsliste(self, jahr, monat):
        von, bis = monatsgrenzen(jahr, monat)
        return len(list(self.db.auftraege.find(
            {"bestelldatum": {"$gte": von.isoformat(), "$lt": bis.isoformat()}},
            {"_id": 1, "bestelldatum": 1})))


ARME = {"A": ArmA, "B": ArmB, "C": ArmC}