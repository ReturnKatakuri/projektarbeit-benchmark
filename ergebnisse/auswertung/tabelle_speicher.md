| Datenmenge | Arm | Daten auf der Platte (MB) | Daten unkomprimiert (MB) | Indizes (MB) | Gesamtbedarf Daten + Indizes (MB) | Verhältnis zum Puffer (1 GB) | pg_database_size (MB) |
|---|---|---|---|---|---|---|---|
| SF1 | A | 167,6 (pg_table_size) | – (keine Entsprechung) | 98,1 (pg_indexes_size) | 265,6 (pg_total_relation_size) | 0,25 | 274,0 |
| SF1 | B | 86,5 (storageSize) | 296,2 (dataSize) | 73,3 (indexSize) | 159,9 (totalSize) | 0,15 | – |
| SF1 | C | 101,0 (storageSize) | 373,1 (dataSize) | 20,6 (indexSize) | 121,6 (totalSize) | 0,11 | – |
| SF2 | A | 1.952,8 (pg_table_size) | – (keine Entsprechung) | 1.142,3 (pg_indexes_size) | 3.095,2 (pg_total_relation_size) | 2,88 | 3.103,6 |
| SF2 | B | 1.013,6 (storageSize) | 3.459,3 (dataSize) | 804,0 (indexSize) | 1.817,6 (totalSize) | 1,69 | – |
| SF2 | C | 1.190,2 (storageSize) | 4.360,3 (dataSize) | 253,2 (indexSize) | 1.443,4 (totalSize) | 1,34 | – |
