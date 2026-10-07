# ModHub (demo)

## Scarica

1. Vai su **[Releases](https://github.com/ibosortino-cmd/modhub/releases)** e scarica **`ModHub.exe`** dall'ultima versione.
2. Fai doppio clic. Non serve installare niente (serve Windows 10 o 11 con Microsoft Edge o Google Chrome).
3. Se Windows mostra "PC protetto da Windows" / "App non riconosciuta": clicca **Ulteriori informazioni** e poi
   **Esegui comunque**. Succede perché l'exe è nuovo e non firmato; se vuoi, confronta prima la sua impronta
   SHA-256 con quella scritta nella pagina della release.

Le impostazioni di ModHub restano in `%APPDATA%\ModHub`. Nella release c'è anche la guida illustrata
(`Guida-ModHub.pdf`). Il codice è libero (licenza MIT); per avviarlo dai sorgenti serve Python 3.11:
`ModHub.cmd`. Per ricreare l'exe: `build_exe.cmd` (con PyInstaller).

ModHub non contiene giochi e non è affiliato agli autori dei giochi o dei port che gestisce.

> **Versione demo.** ModHub è un prototipo: alcune parti vanno ancora riviste e possono esserci errori.
> **I consigli sono benvenuti, anzi richiesti**: cosa non è chiaro, cosa non funziona, cosa manca, quale gioco o mod
> vorresti vedere. Ogni segnalazione aiuta la prossima versione.

**A cosa serve, in breve.** ModHub è un’unica app per i giochi “fatti dalla comunità”: port per PC, mod, patch ed
emulatori. Come una piccola Steam, ma per le mod:

- **Libreria**: tutti i tuoi giochi (port per PC, giochi scaricati, giochi per emulatore) con un pulsante GIOCA.
- **Mod con un clic**: le installi, le aggiorni e le togli dal catalogo; ogni file scaricato viene controllato.
- **Impostazioni semplici** e **Ottimizza per il tuo PC**: grafica, audio e comandi con cursori, livello consigliato
  in base alla scheda video.
- **Emulatori** scaricati e impostati da ModHub.
- **Pannello in gioco** (Ctrl+Shift+Tab): cambi le impostazioni mentre giochi, molte subito.

La guida illustrata, con uno screenshot per ogni funzione, è in [docs/Guida-ModHub.pdf](docs/Guida-ModHub.pdf).

Libreria aperta per mod, port e patch della community: chi crea pubblica, chi gioca installa e
riceve gli aggiornamenti con un clic. Solo libreria standard di Python 3.11, nessuna installazione.

## Provarlo

Doppio clic su `ModHub.cmd`. Si apre una finestra (Edge o Chrome in modalita' app) con l'interfaccia;
chiudendola, il programma si spegne da solo. Se la cartella e' dentro `BT3-Recomp`, trova da solo il gioco.

1. Clic sulla scheda **Mod di prova**: si apre il pannello dettagli. Scegli `1.0.0` e premi *Installa*.
2. Passa alla `1.1.0` e premi *Aggiorna a 1.1.0*: vengono scaricati solo i file cambiati
   (1 modificato + 1 nuovo), quello rimosso sparisce, quello invariato non viene toccato.
3. Installa **Grafica 8x**: compare il profilo di avvio "Grafica 8x" accanto a GIOCA.
4. *Disinstalla* rimuove i file e ripristina gli originali eventualmente sostituiti.

Struttura: `core.py` (motore), `server.py` (server locale + finestra), `ui/` (pagina web: HTML/CSS/JS
senza librerie esterne), `publish.py` (strumento per i creatori).

`ModHub (server locale).cmd` fa la stessa cosa ma servendo il catalogo via HTTP.
Il catalogo predefinito e' quello pubblico su GitHub (https://github.com/ibosortino-cmd/modhub-catalogo); senza
internet ModHub usa la copia inclusa in `public-catalog/`. Dal menu *Catalogo > Cambia sorgente* si puo' usare
qualsiasi URL o cartella (campo vuoto = di nuovo il catalogo predefinito); `catalog/` e' il vecchio catalogo demo.

## Impostazioni del gioco

Nella barra laterale, **Impostazioni** apre un pannello per modificare audio, schermo, grafica, interfaccia,
controller e mod di BT3. Legge e scrive `savedata/settings.toml` (lo stesso file del menu in-game):

- cambia solo le righe che modifichi; commenti, ordine e chiavi sconosciute restano come li ha scritti il gioco;
- prima della prima modifica crea `settings.toml.modhub-bak`; "Ripristina l'originale" lo rimette a posto;
- con il gioco aperto non salva (il gioco riscriverebbe il file all'uscita);
- ogni valore e' validato prima di scrivere e il file risultante viene riletto per controllarne la validita'.

**Ottimizza per il tuo PC** (in cima alle Impostazioni): ModHub riconosce scheda video, memoria video, processore e
RAM e propone un livello su una scala a 5 gradini, da *Prestazioni massime* a *Qualita' massima*. Il cursore parte
dal livello che corrisponde alle impostazioni attuali, un segno indica quello consigliato per il tuo PC; muovendolo
vedi esattamente cosa cambierebbe e *Applica* prepara le modifiche (da confermare con *Salva*, come tutto il resto).
Il livello consigliato e' una stima dal modello della scheda video (tabella in `hardware.py`) corretta per poca
memoria video o poca RAM, non un benchmark. I livelli di ogni gioco stanno nel campo `optimizer` del suo
`settings.json` (5 livelli e la corrispondenza fascia-PC/livello).

Le opzioni (etichette, tipi, intervalli, profili rapidi) sono descritte in `catalog/games/bt3-recomp/settings.json`,
referenziato da `catalog.json`: per un altro gioco basta scrivere un file analogo, senza toccare il programma.

## Altri giochi ed emulatori

- **Aggiungi gioco** (barra laterale): scegli il programma (.exe) di un gioco che hai gia' sul PC e finisce
  nella libreria con la sua icona e il pulsante GIOCA. Se e' un gioco che il catalogo gia' conosce, ModHub
  collega solo la cartella. Il programma non controlla da dove arrivi il file e non lo modifica mai.
- **Emulatori** (barra laterale): elenco incluso in `emulators.json` con PCSX2 (PS2), DuckStation (PS1),
  RPCS3 (PS3), PPSSPP (PSP), Dolphin (GameCube/Wii), Cemu (Wii U), melonDS (DS), mGBA (GBA/GB), Project64 (N64),
  Snes9x e Mesen (SNES/NES), Flycast e Redream (Dreamcast), Xenia (Xbox 360), xemu (Xbox), Stella (Atari 2600).
  Per ognuno: *Scarica e installa* (vedi sotto), *Rileva sul PC* (cerca nelle cartelle solite), *Percorso...*
  (lo scegli tu) o *Sito ufficiale* (apre la pagina nel browser).
  Poi *Aggiungi gioco* (ROM/ISO): il gioco compare nella libreria e GIOCA lancia l'emulatore con i parametri
  giusti, con anche il profilo "Schermo intero" dove l'emulatore lo permette.
- **Download automatico degli emulatori** (15 su 16; Project64 non pubblica build scaricabili in automatico):
  1. *Scarica e installa* mostra cosa verra' scaricato (file, versione, dimensione, sito) e chiede conferma;
  2. il file arriva dal sito ufficiale del progetto (GitHub Releases, dolphin-emu.org, ppsspp.org, redream.io):
     sono contattati solo questi indirizzi, anche nei reindirizzamenti, e solo in HTTPS;
  3. se il progetto pubblica l'impronta SHA-256 (quasi tutti su GitHub) viene verificata, altrimenti la finestra
     lo dice; un file che non corrisponde viene scartato;
  4. l'archivio (.zip o .7z) si apre in una cartella temporanea con controlli su percorsi e collegamenti
     simbolici, poi va in `%APPDATA%\ModHub\emulators\<nome>` (Dolphin, PCSX2 e DuckStation in modalita'
     portatile: impostazioni e salvataggi restano in quella cartella);
  5. *Cerca aggiornamenti* confronta con l'ultima versione; *Aggiorna* sovrascrive solo i file del programma
     e lascia salvataggi e impostazioni; *Disinstalla* cancella la cartella (con avviso).
  Le fonti sono in `emulators.json` (campo `install`: `github`, `json` o `page`).
- **Emulatore personalizzato**: per qualsiasi emulatore non in elenco indichi il programma e gli argomenti
  (ad esempio `-f {rom}`).
- Per aggiungere un emulatore all'elenco incluso basta una voce in `emulators.json` (nome, console, nomi del
  programma, argomenti con `{rom}`, estensioni).
- BIOS, chiavi e simili non sono inclusi e vanno procurati dall'utente; ModHub scarica solo gli emulatori dai
  siti ufficiali dei progetti e non offre ne' supporta modi per scaricare giochi o aggirare protezioni.

## Impostazioni del motore PS2 (PCSX2)

Nella scheda di PCSX2 (sezione Emulatori) e nella pagina di ogni gioco PS2 c'e' **Impostazioni**: stesso
pannello di BT3, con *Ottimizza per il tuo PC* e le sezioni Grafica, Schermo e prestazioni, Audio, Avanzate.
Legge e scrive `PCSX2.ini` (in `Documenti\PCSX2\inis`, oppure accanto al programma in modalita' portatile), cambia
solo le righe toccate, fa una copia `PCSX2.ini.modhub-bak` e non salva a PCSX2 aperto. Le opzioni sono in
`emu_settings/pcsx2.json`; il motore supporta sia TOML sia INI (`"format": "ini"`), quindi altri emulatori si
aggiungono con un file analogo.

## Pannello in gioco e modifiche in tempo reale

Quando avvii un gioco da ModHub, una combinazione di tasti (di partenza **Ctrl+Shift+Tab**, che Windows non usa; la cambi dal menu in basso a
sinistra) apre un piccolo pannello **nativo** (non una finestra del browser), agganciato in basso a destra e sempre in
primo piano, cosi' vedi il gioco mentre cambi le impostazioni. La stessa combinazione, una seconda volta, Esc o la X lo
chiudono e la tastiera torna al gioco. Si puo' trascinare dall'intestazione.

- **Tasti:** Shift+Tab non e' il default perche' BT3 lo usa per il suo menu e PCSX2 per la moviola. La combinazione e'
  registrata **solo mentre il gioco e' in esecuzione**. Se un altro programma la possiede gia', ModHub prova da solo le
  altre (a meno che tu ne abbia scelta una apposta) e il menu mostra quella in uso. Se scegli la stessa combinazione che il
  gioco usa per il suo menu, ModHub non la registra e te lo dice.
- **In tempo reale (PCSX2):** risoluzione (+/-), precisione dei colori, volume, formato immagine, mipmap, filtro TV e
  deinterlaccia cambiano subito nel gioco. PCSX2 non puo' ricevere impostazioni dall'esterno, ma ha dei comandi rapidi:
  ModHub li associa ai tasti F13-F24 (che su una tastiera non esistono, quindi non danno fastidio) aggiungendo poche
  righe alla sezione `[Hotkeys]` del suo `PCSX2.ini` (con copia di sicurezza; le tue scorciatoie restano), avvia PCSX2
  con `-logfile` e manda il tasto alla finestra del gioco.
- **Lettura del risultato, in qualsiasi lingua:** PCSX2 scrive ogni messaggio a schermo nel registro con un nome interno
  uguale in tutte le lingue (`UpscaleMultiplierChanged`...) e testo nella lingua dell'utente. ModHub riconosce il comando
  dal nome interno e legge il valore dalle parti che non cambiano (numeri, testo tra apici, nomi inglesi come `Medium`);
  per gli interruttori (mipmap) la frase e' localizzata, quindi inverte lo stato che conosceva. Per i comandi a scatti
  (risoluzione, precisione colori) non preme mai piu' del necessario e si ferma se il gioco non risponde, se il valore non
  cambia o se la risposta non si capisce.
- **Ottimizza per il tuo PC** funziona anche dal pannello: cio' che ha un comando in tempo reale (risoluzione, precisione
  dei colori) si applica subito, il resto (per esempio il filtro anisotropico) viene salvato e scritto quando il gioco
  si chiude. Tutto quello che scegli e' comunque ricordato per le partite successive.
- **Volume:** i giochi che hanno volumi separati (BT3: generale, musica, effetti) li mostrano come tre cursori nel
  pannello; la modifica e' salvata e si applica alla chiusura del gioco. PCSX2 ha un solo volume: la musica e gli effetti
  separati di un gioco PS2 si regolano nelle opzioni del gioco stesso.
- **Altri giochi/emulatori:** senza comandi in tempo reale le modifiche vengono messe in coda e scritte da ModHub
  appena il gioco si chiude (nella pagina Impostazioni compare "N modifiche in attesa" con *Scarta*).
- **BT3** ha gia' un suo menu in gioco con Shift+Tab: resta cosi', e il pannello di ModHub si apre con la tua combinazione.
  Il port legge `settings.toml` solo all'avvio e non ha un modo per ricevere comandi da fuori (l'ho verificato nel suo
  codice sorgente), quindi nel pannello di ModHub:
  - **Apri il menu del gioco** chiude il pannello, porta il gioco in primo piano e preme Shift+Tab per te: li' audio,
    contorni, ombre, 60 FPS, HUD... cambiano subito (renderer, risoluzione di rendering e bagliore Kaioken no: riavvio).
  - **Tutte le impostazioni** del gioco, divise per gruppi (Audio, Schermo, Grafica, Interfaccia, Controller, Mod e
    avanzate), con gli stessi limiti del gioco (contorni 100-400%, vibrazione 0-200%, HUD +/-120 px...).
  - **Riavvia e applica** (dopo una conferma): chiude il gioco in modo pulito, scrive le modifiche e lo riapre saltando
    il menu iniziale (`PS2X_FE_AUTOPLAY`, chiave `restartEnv` dello schema). La partita in corso si interrompe, i
    salvataggi no. Se il gioco non si chiude entro 20 secondi ModHub non lo forza.
  - Se dopo aver scelto un valore in ModHub lo cambi di nuovo nel menu del gioco, alla chiusura vince la scelta fatta
    nel gioco (e' quella piu' recente).
  - **Subito, anche dal pannello di ModHub:** quasi tutto (segnato «● subito»): audio, controller, contorni (attivi,
    intensita', spessore, colore), ombre, sfocatura, 60 FPS, contatore FPS, texture HD, filtri, bagliore, HUD, obiettivi.
    Il menu del gioco tiene le impostazioni in una struttura in memoria e a ogni fotogramma controlla un segnale
    "modificato": `memlive.py` trova quella struttura (i valori di `settings.toml`, la copia "all'avvio" che la segue e il
    percorso di `settings.toml` dentro l'oggetto, una sola corrispondenza o niente), ci scrive il valore e alza il
    segnale, cosi' il gioco lo applica da solo al fotogramma dopo, come se l'avessi cambiato nel suo menu (che infatti
    mostra il nuovo valore). La prima modifica cerca la struttura (2-3 secondi, fatto in anticipo quando apri il
    pannello), le altre arrivano in pochi millisecondi. Mentre trascini un cursore del volume ModHub scrive direttamente
    i valori letti dal mixer audio (trovati nel programma dai loro valori di partenza), senza far riapplicare tutto.
    Restano al riavvio, come nel menu del gioco: renderer, risoluzione di rendering, bagliore Kaioken, finestra e
    monitor, video introduttivo, stile dei pulsanti, mod. Con un'altra versione del gioco, dove ModHub non trova le
    impostazioni con certezza, non scrive nulla e tutto aspetta il riavvio. Ogni valore e' comunque salvato anche in
    `settings.toml`.
- **Apertura istantanea:** il programma del pannello parte nascosto insieme al gioco (e la scheda video viene
  riconosciuta in anticipo), quindi la combinazione lo mostra in circa 0,2 secondi, gia' della misura giusta, e lo
  nasconde senza chiuderlo; se ne va da solo quando il gioco si chiude.
- **Promemoria all'avvio:** quando la finestra del gioco compare (non il suo menu iniziale), in alto a destra appare per
  30 secondi «Premi Ctrl+Shift+Tab per aprire le impostazioni di ModHub», con una barra del tempo e la ✕ per chiuderlo
  prima. Non prende la tastiera al gioco e sparisce da solo se apri il pannello o chiudi il gioco.
- Non e' l'overlay di Steam: ModHub non disegna dentro il gioco. Funziona su giochi in finestra o a schermo intero senza
  bordi; su un vero schermo intero "esclusivo" Windows puo' nasconderlo (usa *Apri il pannello* dalla finestra principale).
- Il pannello e' `overlay_ui.py` (tkinter, nessuna libreria da installare) e parla con ModHub; il controllo di PCSX2 e' in
  `live.py` e i suoi comandi sono descritti nella sezione `live` di `emu_settings/pcsx2.json`.

## Come pubblica un creatore

```
mia-mod/
    modhub.json        id, name, author, game, version, summary, description, changelog,
                       launch_profiles (opzionale)
    files/             i file, con lo stesso percorso che avranno nella cartella del gioco
        mods/mia_mod.dll
```

```
python publish.py mia-mod --catalog catalog
```

Per un aggiornamento: cambia i file, alza `version` in `modhub.json`, ripubblica. Gli utenti
vedono "Aggiornamento disponibile". Esempi in `examples/`.

## Struttura del catalogo (statica, ospitabile ovunque)

```
catalog.json                    giochi supportati + elenco progetti
projects/<id>/manifest.json     versioni, changelog, elenco file con SHA-256
blobs/<sha256>                  i file (stesso contenuto = un solo file, anche fra versioni)
```

Un file puo' avere un campo `url` per stare altrove (es. GitHub Releases); l'hash viene
verificato comunque. I giochi sono definiti in `catalog.json` (exe da cercare, file da cui
leggere la versione base), quindi aggiungerne uno non richiede modificare il programma.

## Sicurezza (gia' presente)

- Ogni file e' verificato con SHA-256; scarica tutto in una cartella temporanea e applica solo se
  e' tutto valido, quindi un download corrotto non lascia il gioco a meta'.
- Percorsi con `..`, assoluti o con lettera di unita' sono rifiutati.
- Avviso prima di installare file eseguibili (`.dll`, `.exe`, `.cmd`...): il runner di BT3 carica
  ogni `mods/*.dll`.
- Il server locale ascolta solo su 127.0.0.1, richiede un codice casuale per ogni chiamata e controlla
  l'header Host: altre pagine web non possono pilotarlo. L'installazione di file eseguibili e' rifiutata
  dal server se non confermata.
- I file originali sostituiti vanno in `*.modhub-bak` e tornano al loro posto alla disinstallazione.

## Cosa NON c'e' ancora

Account e firma degli autori, upload dal sito, recensioni, download riprendibili, patch binarie
(delta dentro il singolo file), aggiornamento del port base da GitHub, supporto Linux/Mac testato.

Test: `python -m unittest discover -s tests`
