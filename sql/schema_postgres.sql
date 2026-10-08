-- Schema fuer Arm A: PostgreSQL, normalisiert (3NF)
-- Nur Tabellen, Primaer- und Fremdschluessel.
-- Sekundaerindizes stehen in indizes_postgres.sql und werden von
-- load_data.py erst nach dem COPY-Laden angelegt (schneller).
-- Spaltenreihenfolge je Tabelle entspricht exakt der Reihenfolge in den
-- CSV-Dateien, damit COPY ohne Spaltenliste funktioniert.

-- --------------------------------------------------------------------
-- Tabellen in umgekehrter Abhaengigkeitsreihenfolge loeschen,
-- damit das Skript wiederholt ausgefuehrt werden kann.
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS auftragsposition;
DROP TABLE IF EXISTS auftrag;
DROP TABLE IF EXISTS produkt;
DROP TABLE IF EXISTS kunde;

-- --------------------------------------------------------------------
-- Stammdaten
-- --------------------------------------------------------------------

-- Kunden mit Lieferanschrift.
CREATE TABLE kunde (
    kunde_id   INTEGER     PRIMARY KEY,
    name       TEXT        NOT NULL,
    strasse    TEXT        NOT NULL,
    plz        TEXT        NOT NULL,
    ort        TEXT        NOT NULL
);

-- Produktkatalog mit Listenpreis.
-- Der Preis hier ist der aktuelle Katalogpreis; der Preis zum
-- Bestellzeitpunkt steht in auftragsposition.einzelpreis.
CREATE TABLE produkt (
    produkt_id  INTEGER       PRIMARY KEY,
    bezeichnung TEXT          NOT NULL,
    preis       NUMERIC(10,2) NOT NULL
);

-- --------------------------------------------------------------------
-- Bewegungsdaten
-- --------------------------------------------------------------------

-- Ein Auftrag gehoert genau einem Kunden.
CREATE TABLE auftrag (
    auftrag_id   INTEGER  PRIMARY KEY,
    kunde_id     INTEGER  NOT NULL REFERENCES kunde(kunde_id),
    bestelldatum DATE     NOT NULL
);

-- Jede Position eines Auftrags mit Menge und Preis zum Bestellzeitpunkt.
-- Primaerschluessel ist zusammengesetzt: ein Auftrag kann dieselbe
-- Positionsnummer nicht zweimal haben.
CREATE TABLE auftragsposition (
    auftrag_id   INTEGER       NOT NULL REFERENCES auftrag(auftrag_id),
    positionsnr  INTEGER       NOT NULL,
    produkt_id   INTEGER       NOT NULL REFERENCES produkt(produkt_id),
    menge        INTEGER       NOT NULL,
    einzelpreis  NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (auftrag_id, positionsnr)
);

-- --------------------------------------------------------------------
-- Sekundaerindizes: siehe sql/indizes_postgres.sql
--
-- Die drei Indizes (idx_auftrag_kunde_id, idx_auftrag_bestelldatum,
-- idx_auftragsposition_produkt_id) stehen bewusst in einer eigenen
-- Datei und werden erst nach dem Laden der Daten angelegt. Wuerden
-- sie hier stehen, muesste PostgreSQL jeden Index bei jeder
-- eingefuegten Zeile mitpflegen -- bei mehreren Millionen Positionen
-- ein erheblicher Mehraufwand. Nach dem Laden baut PostgreSQL den
-- B-Tree stattdessen in einem einzigen Sortierdurchlauf auf, was
-- deutlich schneller ist.
--
-- Zwingend einzuhaltende Reihenfolge:
--   1. schema_postgres.sql  (diese Datei)  -- Tabellen anlegen
--   2. COPY                                -- Daten laden
--   3. indizes_postgres.sql                -- Indizes aufbauen
--   4. ANALYZE                             -- Statistiken aktualisieren
--
-- load_data.py fuehrt diese vier Schritte automatisch in der
-- richtigen Reihenfolge aus.
-- --------------------------------------------------------------------

