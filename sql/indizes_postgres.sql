-- Sekundaerindizes fuer Arm A: PostgreSQL.
-- Wird von load_data.py nach dem COPY-Laden ausgefuehrt.
-- Reihenfolge Schema -> COPY -> Indizes -> ANALYZE:
-- PostgreSQL baut den B-Tree in einem Sortierdurchlauf auf den fertigen
-- Daten -- das ist deutlich schneller, als den Index waehrend des Ladens
-- bei jedem INSERT mitzupflegen.
-- Die drei Indizes entsprechen exakt denen in mongo_setup.py fuer Arm B
-- und C, damit alle Messarme dieselben Zugriffswege abgedeckt haben.

-- L1, L3: alle Auftraege eines Kunden schnell finden.
CREATE INDEX idx_auftrag_kunde_id
    ON auftrag(kunde_id);

-- L4 (Q1, Q2): Auftraege nach Zeitraum filtern.
CREATE INDEX idx_auftrag_bestelldatum
    ON auftrag(bestelldatum);

-- L4 (Q1): Positionen eines Produkts aggregieren.
CREATE INDEX idx_auftragsposition_produkt_id
    ON auftragsposition(produkt_id);
