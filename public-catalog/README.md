# Catalogo pubblico di ModHub

> **Versione demo.** ModHub è un prototipo: alcune parti vanno ancora riviste e possono esserci errori.
> **I consigli sono benvenuti, anzi richiesti**: cosa non è chiaro, cosa non funziona, cosa manca, quale gioco o mod
> vorresti vedere. Ogni segnalazione aiuta la prossima versione.

ModHub è un’app per Windows che installa e aggiorna con un clic mod, patch e profili per i giochi della comunità (port per PC ed emulatori), e permette di cambiarne le impostazioni anche durante la partita.
I consigli si lasciano nelle **[Issues di ModHub](https://github.com/ibosortino-cmd/modhub/issues)** (nell'app: Informazioni → *Lascia un consiglio*).

Questo repository è il catalogo che ModHub legge per mostrare mod, patch e profili da installare con un clic.
Non contiene giochi: ognuno usa la propria copia (per BT3 Recompiled: il port dal progetto ufficiale
<https://github.com/z3xox/BT3-Recomp> e i dati della propria ISO).

- `catalog.json`: elenco dei giochi e dei progetti
- `projects/<id>/manifest.json`: versioni, note di rilascio, file e profili di avvio di ogni progetto
- `blobs/<sha256>`: i file, chiamati con la loro impronta SHA-256 (ModHub controlla ogni download)
- `games/<id>/settings.json`: le impostazioni del gioco che ModHub sa modificare

È il catalogo **predefinito** di ModHub: non serve impostare niente. Il suo indirizzo per ModHub è
`https://raw.githubusercontent.com/ibosortino-cmd/modhub-catalogo/main/` (in *Catalogo > Cambia sorgente*, campo vuoto =
questo catalogo).

Per pubblicare una mod o una nuova versione: `python publish.py <cartella-mod> --catalog public-catalog`,
poi `python catalog_upload.py <utente>/<repository>` (dalla cartella di ModHub).
