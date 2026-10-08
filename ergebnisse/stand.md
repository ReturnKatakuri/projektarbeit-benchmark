# Stand der Messungen

Diese Datei wird nach jeder Messreihe aktualisiert. Letzte Aktualisierung:
02.10.2026.

Rohdaten: `ergebnisse/messungen.csv` (Messreihe), `ergebnisse/diagnose.csv`
(Test-, Probe- und Diagnoseläufe), `ergebnisse/speicherbedarf_sf1.txt`,
`ergebnisse/speicherbedarf_sf2.txt`, `ergebnisse/run_all.log`. Bei Abweichungen gelten die Rohdaten.

## Messreihe

Seit dem 01.10.2026 läuft der Lastgenerator in einem Client-Container im
Docker-Netz der Datenbanken (4 CPUs, 1,5 GB) statt auf dem Host, und jeder der
vier Clients ist ein eigener Prozess statt eines Threads. Ebenfalls seit dem
01.10. ist in Arm B und C die Journal-Bestätigung aktiv (`journal=True`), und
die Zufallsauswahl hat je Prozess einen festen Startwert (Laufnummer mal 100
plus Prozessnummer). Die Messreihe
wird in diesem Aufbau vollständig neu gemessen, damit alle Arme unter
identischen Bedingungen gemessen werden.

Stand 02.10.: Die Messreihe ist vollständig. `messungen.csv` enthält 150 gültige
Läufe von 150, alle ohne Fehler und jede Kombination genau einmal. 145 Läufe
stammen aus der Nacht vom 01. auf den 02.10. (22:58 bis 06:45 Uhr), fünf wurden
am 02.10. zwischen 10:14 und 10:31 Uhr wiederholt:

- SF2, L1, Arm B, Lauf 4 und 5 (`run_all.py --nur-sf2 --profil L1`): In der
  Nacht stürzte MongoDB während Lauf 4 ab (siehe Abschnitt „Absturz von
  MongoDB"). Die drei betroffenen Zeilen stehen in
  `ergebnisse/verworfen_sf2_mongodb.csv`. Die Wiederholungen liegen mit 3.820
  und 3.770 Ops/s im Bereich der Läufe 1 bis 3 (3.721 bis 3.820 Ops/s). Anders
  als in der Nacht ging Lauf 4 ein Neustart des Containers unmittelbar voraus.
- SF1, L3, Arm C, Fan-out 1000, Lauf 2 bis 4 (`run_all.py --nur-sf1 --profil
  L3`): In der Nacht startete `bench.py` nicht, weil `client_container.sh` vor
  jedem Lauf `docker build` ausführte und Docker Hub zwischen 04:09 und
  04:11 Uhr nicht erreichbar war (`run_all.log`, „failed to resolve source
  metadata"); es entstanden keine Ergebniszeilen. Seit dem 02.10. wird das
  Image nur noch gebaut, wenn es fehlt. Für die Wiederholung wurde SF1 neu
  geladen, die Dumps wurden neu erzeugt und das Wiederherstellen geprüft.

Nach der Messreihe liegt SF1 in den Datenbanken, der Bestand ist sauber.

Die Auswertung der 150 Läufe (Verdichtung, Effekte, Hypothesen, Schwellenwert,
Speicherbedarf, Abbildungen) steht in `ergebnisse/auswertung/`, der Bericht in
`ergebnisse/auswertung/bericht.md`. Sie wird von `skripte/auswertung.py`
erzeugt und liefert bei wiederholtem Aufruf dieselben Dateien.

## Absturz von MongoDB bei SF2, L1, Arm B (02.10.)

Um 04:50:03 Uhr (02:50:03 UTC) beendete sich der MongoDB-Prozess selbst mit
einer fatalen internen Zusicherung:
`Invariant failure: request->recursiveCount > 0` in
`src/mongo/db/concurrency/lock_manager.cpp:487` (`LockManager::unlock`),
danach `Got signal: 6 (Aborted)`. Die betroffene Operation war ein `find` auf
`arm_b.produkte`, also die vierte Abfrage von L1 in Arm B. Beim nächsten Start
um 04:57:11 Uhr meldete MongoDB `Detected unclean shutdown - Lock file is not
empty` und stellte den Bestand aus dem Journal wieder her (66 ms). Hinweise
auf das Speicherlimit gibt es nicht: keine Meldung zu Speichermangel im
MongoDB-Log, kein OOM-Eintrag im Kernel-Log der Docker-VM für diesen Zeitraum,
und die Ursache ist im Log ausdrücklich als fehlgeschlagene Zusicherung
benannt. Der Auszug des MongoDB-Logs liegt in
`ergebnisse/mongodb_absturz_20261002.log`.

Folgen in der Messreihe (`run_all.log`, 04:48 bis 04:57 Uhr): Lauf 4 lief seit
04:48:02 Uhr; der Absturz fiel in die Mitte seines Messfensters. Die Zeile ist
formal gültig (8 Fehler bei 221.246 Operationen), hat aber mit 1.829 Ops/s nur
etwa den halben Durchsatz der Läufe 1 bis 3 (3.721 bis 3.820 Ops/s). Lauf 5 und
seine Wiederholung liefen gegen den gestoppten Container (`Name or service not
known`, 0 Operationen). Erst der planmäßige Neustart vor Arm C startete
MongoDB wieder; der Bestand war danach vollständig (Anzahlen = kennzahlen.json).

Im lesbaren Teil des Container-Logs (29.09. bis 01.10. 22:33 Uhr und ab 02.10.
03:29 Uhr) ist es der einzige Absturz dieser Art, bei 98 Läufen gegen MongoDB
in der Messreihe. `docker logs` bricht beim Vorwärtslesen am 01.10. um
22:33 Uhr ab; die späteren Zeilen sind nur mit `--tail` lesbar.

## Probelauf im neuen Aufbau (01.10., Laufnummer 53)

`run_all.py --probe --ohne-sf2 --lauf-nummer 53`: 21 Läufe mit 10 s Aufwärmen
und 20 s Messung, alle ohne Fehler, Dauer 13 min 47 s. Client-Container mit
4 CPUs und 1,5 GB, je Client ein Prozess. Die Zeilen stehen in `diagnose.csv`.
Vorher wurde SF1 über den Client-Container neu geladen (35 s, Anzahlen und
Indizes stimmen).

CPU-Auslastung während der Messfenster (`docker stats`, 6 bis 7 Stichproben je
Lauf, Höchstwert; Limit Client 400 %, Datenbank 400 %):

| Lauf | Durchsatz (Ops/s) | Median (ms) | Client | Datenbank |
|---|---|---|---|---|
| L1 A | 29.248 | 0,130 | 200 % | 157 % |
| L1 B | 4.478 | 0,866 | 231 % | 86 % |
| L1 C | 18.452 | 0,203 | 242 % | 72 % |
| L4-Q1 A | 49,4 | 87,8 | 10 % | 403 % |
| L4-Q2 A | 839 | 4,74 | 217 % | 307 % |
| L4-Q1 B | 32,4 | 123,2 | 34 % | 366 % |
| L4-Q2 B | 230 | 17,2 | 108 % | 287 % |
| L4-Q1 C | 56,0 | 71,0 | 16 % | 384 % |
| L4-Q2 C | 200 | 19,9 | 97 % | 304 % |
| L2 A | 6.765 | 0,579 | 144 % | 111 % |
| L2 B | 8.953 | 0,431 | 232 % | 104 % |
| L2 C | 5.593 | 0,665 | 233 % | 99 % |
| L3 A, Fan-out 10 / 100 / 1000 | 14.332 / 15.100 / 15.632 | 0,265 / 0,254 / 0,249 | 88 / 78 / 77 % | 93 / 79 / 82 % |
| L3 B, Fan-out 10 / 100 / 1000 | 19.496 / 19.287 / 19.403 | 0,193 / 0,195 / 0,193 | 228 / 228 / 229 % | 92 / 93 / 92 % |
| L3 C, Fan-out 10 / 100 / 1000 | 7.837 / 2.983 / 393 | 0,498 / 1,341 / 9,872 | 193 / 74 / 11 % | 148 / 303 / 398 % |

Der Client bleibt in allen Läufen unter seinem Limit (Höchstwert 242 % von
400 %); sein Arbeitsspeicher lag bei höchstens 118 MiB. Gegenüber dem Probelauf
mit Threads und 2 CPUs (Laufnummer 52, ebenfalls in `diagnose.csv`) ist die
Datenbank bei L1 deutlich höher ausgelastet: Arm A 157 % statt 55 %, Arm B 86 %
statt 26 %, Arm C 72 % statt 19 %. Der Durchsatz bei L1 stieg von 10.835 auf
29.248 (A), von 1.303 auf 4.478 (B) und von 4.808 auf 18.452 Ops/s (C), der
Median fiel von 0,322 auf 0,130 ms (A), von 2,947 auf 0,866 ms (B) und von
0,714 auf 0,203 ms (C).

Bei L1, L2 und L3 in Arm B verbraucht der Client weiterhin mehr Rechenzeit als
die Datenbank. Keine der beiden Seiten ist dort an ihrem Limit; die gemessene
Antwortzeit enthält bei diesen kurzen Operationen einen spürbaren Anteil des
Python-Treibers.

## Journal-Bestätigung und fester Startwert (01.10., Laufnummer 54)

Bis Probelauf 53 öffnete `queries.py` die Verbindung zu MongoDB ohne Angabe zur
Schreibbestätigung. Auf einem einzelnen Knoten gilt dann `w: 1` ohne `j: true`:
Ein Schreibvorgang wird bestätigt, bevor er im Journal auf der Platte steht.
PostgreSQL lief dagegen mit `synchronous_commit=on`. Seit dem 01.10. öffnen
Arm B und Arm C den Client mit `journal=True`.

Nachweis im laufenden System: Client und Collections tragen die Write Concern
`{j: true}`, der gesendete Befehl enthält `writeConcern: {j: true}`, und der
Server synchronisiert das Journal bei jeder Einfügung (301 Synchronisierungen
bei 300 Einfügungen, ohne die Einstellung 3).

Wirkung, `run_all.py --probe --ohne-sf2 --profil L2,L3 --lauf-nummer 54`
(12 Läufe, 0 Fehler, Zeilen in `diagnose.csv`), Median gegenüber Lauf 53:

| Lauf | Median Lauf 53 (ms) | Median Lauf 54 (ms) | Änderung | Durchsatz 53 → 54 (Ops/s) |
|---|---|---|---|---|
| L2 A | 0,579 | 0,566 | −2 % | 6.765 → 6.889 |
| L2 B | 0,431 | 1,020 | +137 % | 8.953 → 3.864 |
| L2 C | 0,665 | 0,937 | +41 % | 5.593 → 4.178 |
| L3 A, Fan-out 10 | 0,265 | 0,271 | +2 % | 14.332 → 14.173 |
| L3 A, Fan-out 100 | 0,254 | 0,272 | +7 % | 15.100 → 14.141 |
| L3 A, Fan-out 1000 | 0,249 | 0,274 | +10 % | 15.632 → 14.063 |
| L3 B, Fan-out 10 | 0,193 | 0,438 | +127 % | 19.496 → 8.283 |
| L3 B, Fan-out 100 | 0,195 | 0,448 | +130 % | 19.287 → 8.107 |
| L3 B, Fan-out 1000 | 0,193 | 0,442 | +129 % | 19.403 → 8.229 |
| L3 C, Fan-out 10 | 0,498 | 1,431 | +187 % | 7.837 → 2.750 |
| L3 C, Fan-out 100 | 1,341 | 2,505 | +87 % | 2.983 → 1.580 |
| L3 C, Fan-out 1000 | 9,872 | 11,543 | +17 % | 393 → 349 |

Lauf 53 und 54 unterscheiden sich außer in der Journal-Bestätigung auch im
Startwert der Zufallsauswahl; beides sind Probeläufe mit 20 s Messfenster.
In Arm B und C wartet jede Schreibanweisung einzeln auf das Journal. L2 in B
(Auftrag und Positionen) und L3 in C (Stammdatum und Aufträge) bestehen aus
zwei Anweisungen und warten deshalb zweimal, Arm A je Transaktion einmal.

## Verworfene Messreihen (nicht Teil der Auswertung)

`ergebnisse/verworfen_sf2_mongodb.csv`: SF2, L1, Arm B, Lauf 4 sowie Lauf 5
mit seiner Wiederholung (02.10., 04:51 bis 04:57 Uhr). MongoDB stürzte während
Lauf 4 ab; die Zeile von Lauf 4 ist formal gültig, enthält aber nur das halbe
Messfenster. Beleg: `run_all.log` ab 04:48:02 Uhr und
`mongodb_absturz_20261002.log`. Beide Läufe wurden am 02.10. wiederholt.

`ergebnisse/verworfen_host_client.csv`: Messreihe mit Lastgenerator auf dem
Host (29.09. bis 01.10., 91 Zeilen), abgebrochen, weil die Portweiterleitung
von Docker Desktop bei anhaltendem Verkehr zu MongoDB ausfiel. Nicht Teil der
Auswertung, aber Beleg für die Fehleranalyse.

`ergebnisse/verworfen_q1b_clientseitig.csv`: alle Läufe von L4 in Arm B aus
der Messreihe vom 30.09. (1 gültiger Q1-Lauf, 18 Fehlversuche). Sie entstanden
mit der clientseitigen Umsetzung von Q1 in Arm B, die 11,5 MB je Operation
übertrug und die Docker-Portweiterleitung überlastete. Seit dem 30.09.
aggregiert Q1 in Arm B blockweise auf dem Server; A, B und C liefern für alle
zwölf Monate 2024 dieselben Produkte und Umsatzsummen.

`ergebnisse/verworfen_20260929_docker.csv`: Sicherung einer am 29.09.
abgebrochenen Messreihe, die wegen eines Docker-Fehlers verworfen und
vollständig neu gemessen wurde. Die Fehlversuche bei L4 Arm B darin (ab
21:23 Uhr: 0 Operationen, 16 Fehler je Lauf) gehen auf dieselbe Ursache zurück.

## Fehleranalyse: Ausfall der Portweiterleitung

Mit dem Lastgenerator auf dem Host brach die Verbindung zu MongoDB an drei
Tagen mit demselben Muster ab: Der erste Lauf einer datenintensiven Abfrage in
Arm B gelang, ab dem zweiten gab es 0 Operationen und genau 16 Fehler
(`ServerSelectionTimeoutError: localhost:27017: [Errno 54] Connection reset by
peer`), danach antwortete die Docker-API mit Fehler 500. Die 16 Fehler ergeben
sich aus dem Standard-Timeout von pymongo (30 s): 4 Clients mal 4 Versuche in
120 s. MongoDB selbst lief weiter (ExitCode 0, kein OOM, keine Fehler im
Container-Log).

- 29.09. und 30.09.: Q1 in Arm B in der clientseitigen Umsetzung (rund 11,5 MB
  je Operation); Q2 schlug mit, weil die Verbindung bereits unterbrochen war.
- 30.09., Reproduktion: Ausfall im zweiten von drei Läufen à 60 s.
- 01.10.: Q1 in Arm B lief serverseitig fünfmal sauber, danach fiel Q2 in
  Arm B ab dem zweiten Lauf aus.

Gegenprobe am 01.10. (Zeilen mit Lauf 99 in `diagnose.csv`): Q2 in Arm B aus
dem Client-Container, dreimal 60 s Aufwärmen und 120 s Messung direkt
hintereinander: 20.016, 19.464 und 20.595 Operationen, 0 Fehler, Median 22 bis
23 ms. arm_mongodb sendete dabei zusammen 56,3 GB, rund 105 MB/s. Der
Datenstrom war damit mindestens so groß wie in den gescheiterten Läufen vom
Host (rechnerisch etwa 85 MB/s), ohne Ausfall.

Offen bleibt, warum Q2 in Arm A über dieselbe Portweiterleitung zehnmal ohne
Ausfall lief (467 Ops/s bei gleicher Zeilenzahl je Operation). Beobachtet wurde
der Ausfall nur beim Verkehr zu MongoDB.

## Speicherbedarf SF2

Erfasst am 02.10. in `ergebnisse/speicherbedarf_sf2.txt`, bei vollständig
geladenem SF2. Gesamtbedarf aus Daten und Indizes: Arm A 3.095,2 MB
(pg_total_relation_size), Arm B 1.817,6 MB, Arm C 1.443,4 MB (jeweils
totalSize = storageSize + indexSize). Das ist das 11,7-, 11,4- und 11,9-Fache
von SF1. Wie bei SF1 ist C bei dataSize (4.360 gegen 3.459 MB) und storageSize
(1.190 gegen 1.014 MB) größer als B, bei indexSize kleiner (253 gegen 804 MB).

Gemessen am Puffer von 1 GB belegt Arm A das 2,9-Fache, Arm B das 1,7-Fache und
Arm C das 1,3-Fache; das Speicherlimit der Container liegt bei 2 GB. Nur Arm A
übersteigt damit auch das Containerlimit. Für die Aussage, SF2 übersteige den
Arbeitsspeicher um ein Mehrfaches, ist das bei B und C zu beachten.

## Speicherbedarf SF1

Getrennte Erfassung von dataSize, storageSize und indexSize am 30.09. in
`ergebnisse/speicherbedarf_sf1.txt`. Über alle drei Arme vergleichbar ist nur
der Gesamtbedarf aus Daten und Indizes: Arm A 265,6 MB
(pg_total_relation_size), Arm B 159,9 MB, Arm C 121,6 MB (jeweils totalSize =
storageSize + indexSize). C ist sowohl bei dataSize (373 gegen 296 MB) als auch
bei storageSize (101 gegen 87 MB) größer als B. Die Reihenfolge kehrt sich erst
durch die Indizes um: indexSize B 73 MB, C 21 MB, weil B 2,9 Mio.
Positionsdokumente indiziert und C nur 300.000 Aufträge. Alle Kennzahlen sind
getrennt und mit ihrem Namen zu berichten; für PostgreSQL gibt es keine
Entsprechung zu dataSize und storageSize. Die Werte hängen nicht vom
Lastgenerator ab.
