# UX- und Design-Review des Dashboards

Stand: `main` bei `84024b5`, gelesen wurden alle Templates unter `sc_digger/web/templates/`.
Belege stehen als `Datei:Zeile` (ohne Pfad-Präfix). Maßstab sind die vier Handgriffe: **(1)** Digest
durchhören und bewerten, **(2)** Inbox prüfen und übernehmen, **(3)** Duplikate und Kaufliste
abarbeiten, **(4)** ein harmonisches Set bauen. Kontrastwerte sind nach WCAG aus den Farbwerten in
`base.html:10-42` gerechnet. Die Mockups liegen in [`mockups/`](mockups/).

## Review

**1. Task-Fluss.** Handgriff 1 hat keinen durchgehenden Weg: Die Digest-Tabelle hat keinen Player,
Anhören heißt „SoundCloud ↗“ in einem neuen Tab je Track (`digest.html:307-310`). Vorhören gibt es
nur im Track-Explorer (`tracks.html:487`). Handgriff 2 ist dort gut erreichbar
(`status.html:146`), die Übernahme selbst ist aber nur eine Anzeige (`tracks.html:493-500`).
Handgriff 3 verteilt sich auf vier Orte: „Kaufliste (nur Store)“ im Digest (`digest.html:89`), die
Seite Kaufliste (`base.html:983`), Gates im Digest (`digest.html:316-319`) und den Upload; Duplikate
liegen hinter „Mehr“ (`base.html:984-990`). Handgriff 4: Der Match-Link (`tracks.html:532`) landet
auf `/mix` über dem Set-Builder (`mix.html:97`), das Ergebnis steht eine Karte tiefer (`mix.html:141`).

**2. Informationsdichte.** Der Track-Explorer hat 13 Spalten (`tracks.html:382-408`), die Titelzelle
bis zu fünf Zeilen mit Pfad, Plays und Badges (`tracks.html:420-442`). Entscheidungen tragen BPM,
Key, Energie, Break, Taste und der Player. Dekoration sind der Pfad in jeder Zeile
(`tracks.html:422`), das Badge „OK“ in jeder Zeile (`tracks.html:460-461`) und „Übernahme“ neben
„Status“ (`tracks.html:470-500`). Energie steht als nackte Ziffer (`tracks.html:451-453`). Im Digest
stehen Plays und Likes doppelt (`digest.html:256-259`, `299-300`), dazu drei Korrektur-Knöpfe je
Zeile (`digest.html:281-285`). Die Statusseite nennt die Rohtreffer dreimal (`status.html:203`,
`209`, `424`) und die Inbox-Größe viermal (`status.html:146`, `235`, `242`, `313`).

**3. Zustände.** Gut: leere Listen mit Rücksetz-Link (`_components.html:2-7`), „Taste-Modell: noch
nicht trainiert“ (`tracks.html:359`), Stand und Alter des Laufs (`status.html:138`, `204`). Lücken:
Das Nachladen der Tabelle zeigt nichts an (`tracks.html:851-882`, `digest.html:475-503`) (umgesetzt in #603) und ersetzt
die `<audio>`-Elemente, die Wiedergabe endet still (`tracks.html:866`). Der Fehlertext im
Ähnlich-Panel nutzt die nicht definierte Variable `--err` (`tracks.html:43`) (umgesetzt in #603). Inbox-Dubletten laden
ohne Hinweis, nach einem Fehler bleiben die Gruppen-Knöpfe gesperrt (`duplicates.html:275`,
`293-295`). Der Link-Scanner zeigt leer nur einen Tabellenkopf (`mix.html:288-302`). Datenstand und
Alarme sieht man nur auf der Statusseite; deren Auto-Refresh (`status.html:7`) löscht die Meldung
des Rekordbox-Exports (`status.html:468-477`).

**4. Tastatur.** Es gibt einen Fokusring (`base.html:63`), die drei Erklär-Klapper schalten ihn ab
(`base.html:817`). Shortcuts existieren nur im Track-Explorer und nur für den Player: Leertaste,
Pfeile, J/K (`tracks.html:949-966`). Bewerten geht nicht per Taste. Die Leertaste wird auch auf
fokussierten Knöpfen abgefangen (`tracks.html:950-954`). Die ausgeblendete Player-Leiste bleibt im
Tab-Weg (`tracks.html:91`, `589-594`), ihre Knöpfe haben nur `title`, der Regler keinen Namen. Je
Zeile liegen bis zu sieben Tab-Stopps (`tracks.html:487`, `504-518`, `530-535`). Die Bewertungs-Knöpfe
im Digest haben weder `aria-label` noch `aria-pressed` (`digest.html:290-295`).

**5. Kleine Viewports.** Unter 900 px wird jede Tabelle zur Kartenliste, der Tabellenkopf
verschwindet (`base.html:945-954`) und mit ihm die Sortierung (`tracks.html:382-404`). Ein Track
ergibt 13 Felder untereinander; vier haben ein leeres Label (`tracks.html:481`, `484`, `493`, `501`),
und die Titelzelle wird zur Flex-Zeile, in der Titel, Pfad und Badges nebeneinander stehen
(`base.html:950`, `tracks.html:420-442`). Tabellen ohne `data-label` zeigen Werte ohne Beschriftung
(`status.html:415-425`, `kaufliste.html:94-130`, `rejected.html:102-130`, `duplicates.html:89-114`,
`mix.html:246-254`). Der Kopf klebt oben und bricht mehrzeilig um (`base.html:79-93`, `962`); die
Player-Leiste bricht ebenfalls um und verdeckt mehr als die 5rem Reserve (`tracks.html:97-105`,
`base.html:192`).

**6. Konsistenz.** „Inbox“ ist im Track-Explorer grün, im Digest blau; „Sammlung“ umgekehrt
(`tracks.html:472-474`, `digest.html:274-276`). Die Legende des Track-Explorers widerspricht der
eigenen Tabelle (`tracks.html:245-249`), Duplikate nutzen Lila und englische Rohwerte
(`duplicates.html:91-95`). `.warn` ist rot, `.badge-warn` gelb (`base.html:265-268`, `282`, `309`).
Begriffe: Quarantäne, abgelehnt, abgewiesen, aussortiert (`rejected.html:42`, `44`, `86`,
`kaufliste.html:47`); Duplikate und Dubletten (`duplicates.html:42`, `168`); Künstler und Artist
(`duplicates.html:77`); Runs und Läufe (`status.html:345`, `398`); „Zurück/Weiter“ gegen
„Vorherige/Nächste“ (`tracks.html:571-572`, `digest.html:347-350`); englische Titel wie „Harmonic
Mix Finder“ (`mix.html:143`). Emoji und SVG-Icons mischen sich (`digest.html:291`,
`tracks.html:508`), ⏳ steht für Gate und für „Später“ (`digest.html:267`, `295`). Prozentwerte werden
als 0–1 eingegeben, aber als % beschriftet (`tracks.html:327-335`).

**7. Kontrast und Lesbarkeit.** Die Textfarben sind solide: 16,8:1, 7,2:1 und 6,0:1 auf der Karte
(`base.html:22-24`). Schwach ist Rot als Schrift: `#ef4444` erreicht 4,9:1 auf der Karte, 4,4:1 in
der roten Status-Pille (`base.html:265-268`) und 4,0:1 im gelben Alarmkasten (`status.html:174-175`);
die Badges nutzen das hellere `#f87171` (6,0:1). Die laufende Zeile hebt sich nur mit 1,1:1 vom
Hintergrund ab (`base.html:460-462`). Deaktivierte Blätter-Links fallen auf 3,5:1 (`base.html:572-575`).
Kleinste Schrift ist 0,68rem (`base.html:130`, `874`). „Fehler“ und „Abgelehnt“ sind immer rot, auch
bei 0 (`status.html:277`, `366`).

**8. Rückmeldung.** Es gibt Toasts (`base.html:1005-1018`), aber nicht überall: Bewertungen im
Digest bleiben stumm (`digest.html:376`), im Track-Explorer und in der Kaufliste nicht
(`tracks.html:630`, `kaufliste.html:240`). Die Matcher-Korrektur meldet sich, das Status-Badge der
Zeile bleibt unverändert (`digest.html:405-409`). Der Rekordbox-Export schreibt in ein ungestaltetes
`<span>` (`status.html:151`, `477`), das Verschieben von Dubletten nutzt `confirm()` und eine
Textzeile (`duplicates.html:195`, `286`). Der Toast liegt über der Player-Leiste
(`base.html:907-911`, `tracks.html:79-90`). Vorbildlich ist die Freigabe aus der Quarantäne: Zeile
gedimmt, „✓ freigegeben“, Toast (`rejected.html:179-184`).

## Befunde

Sortiert nach Nutzen im Verhältnis zum Aufwand. Aufwand: S = nur Templates, bis ein halber Tag;
M = mehrere Templates oder kleine Route; L = neue Route oder neuer Zustand.

**1. Bewerten geht nicht per Taste und springt nicht weiter** (umgesetzt in #576)
- Beleg: `tracks.html:949-966`, `tracks.html:504-518`, `digest.html:290-295`
- Beobachtung: Der Player hat Shortcuts, die Bewertung nicht. Wer 30 Tracks durchhört, greift 30-mal
  zur Maus oder tabbt durch drei Knöpfe je Zeile.
- Vorschlag: `+` gefällt, `−` passt nicht, `S` später, jeweils für den laufenden Track; danach
  startet der nächste. `Z` nimmt die letzte Bewertung zurück.
- Aufwand: S. Nutzen: Handgriff 1 und 2 werden zu einer Taste je Track.

**2. Tastatur-Fallen im Player** (umgesetzt in #576)
- Beleg: `tracks.html:950-954`, `tracks.html:91`, `tracks.html:589-594`, `base.html:817`
- Beobachtung: Leertaste auf einem fokussierten Knopf („Ähnlich“, 👍, Spaltenwahl) startet oder
  pausiert zusätzlich die Wiedergabe; je nach Browser löst der Knopf gar nicht aus. Die unsichtbare
  Player-Leiste nimmt sechs Tab-Stopps ein. Die Erklär-Klapper haben keinen Fokusring.
- Vorschlag: Shortcuts nur auslösen, wenn das Ziel kein `button`, `a`, `summary` oder Formularfeld
  ist; die Leiste mit `visibility: hidden` ausblenden; `outline: none` streichen; den Knöpfen
  `aria-label` geben.
- Aufwand: S. Nutzen: alle Handgriffe, die Tastatur wird verlässlich.

**3. Der Digest bewertet stumm** (umgesetzt in #576)
- Beleg: `digest.html:359-382`, `digest.html:290-295`; Vergleich `tracks.html:625-634`
- Beobachtung: Dieselbe Funktion `sendFeedback` steht dreimal im Code. Die Digest-Kopie zeigt keinen
  Toast, setzt kein `aria-pressed` und nutzt Emoji statt der Icons aus `_icons.html`.
- Vorschlag: eine gemeinsame Fassung in `base.html`, die Knöpfe als Makro in `_components.html`.
- Aufwand: S. Nutzen: Handgriff 1; die häufigste Aktion bestätigt sich sichtbar.

**4. Statusfarben, Begriffe und Einheiten widersprechen sich**
- Beleg: `tracks.html:472-474`, `digest.html:274-276`, `tracks.html:245-249`,
  `duplicates.html:91-95`, `base.html:282`, `tracks.html:327-335`
- Beobachtung: Inbox ist je nach Seite grün oder blau, „warn“ je nach Klasse rot oder gelb. Für
  abgelehnte Tracks gibt es vier Wörter. „Max. Breakdown %“ erwartet `0.2`.
- Vorschlag: ein Makro `status_badge` mit fester Zuordnung (Inbox blau, Sammlung grün, abgelehnt
  rot); ein Wort je Sache (Quarantäne, Duplikate, Artist, Läufe); Prozentfelder in Prozent; Rot als
  Schrift auf `#f87171`.
- Aufwand: S. Nutzen: alle Handgriffe; Farbe wird wieder zum Signal.

**5. Alarm und Datenstand stehen nur auf der Statusseite** (Ortszeit für Digest, Tracks, Kaufliste und Quarantäne umgesetzt in #621)
- Beleg: `status.html:173-182`, `base.html:977-991`, `status.html:7`
- Beobachtung: Wer morgens direkt den Digest öffnet, sieht nicht, dass der Lauf gescheitert ist und
  die Liste von gestern stammt. Alle Zeiten stehen in UTC (`status.html:138`, `405`).
- Vorschlag: Alarm-Zähler im Kopf jeder Seite, „Stand“ neben der Trefferzahl, Zeiten in Ortszeit.
  Auf der Statusseite den Seiten-Refresh durch Nachladen der Kacheln ersetzen.
- Aufwand: S bis M (der Zähler muss in den Kontext jeder Seite). Nutzen: Handgriff 1 und 2.

**6. Vom Track zum Set: Sprungziel und Export** (Sprungziel, Export und Scanner-Leerzustand umgesetzt in #604)
- Beleg: `tracks.html:532`, `mix.html:97`, `mix.html:141`, `mix.html:133`, `mix.html:633-634`,
  `mix.html:650-660`
- Beobachtung: „Match“ landet über dem Ergebnis. Die Trefferliste hat weder Vorhören noch Status
  noch Energie (`mix.html:236-240`). Der M3U8-Link lädt schon vor der Berechnung ein Set; der
  XML-Export liest das Formular neu und kann vom angezeigten Set abweichen.
- Vorschlag: Mix-Finder nach oben oder Sprungmarke am Link; Export-Knöpfe erst nach der Berechnung
  aktiv und an deren Parameter gebunden; Abspielknopf in der Trefferliste.
- Aufwand: S (Vorhören M). Nutzen: Handgriff 4.

**7. Track-Tabelle: 13 Spalten, hohe Zeilen, ein Player je Zeile** (ein Player statt `<audio>` je Zeile umgesetzt in #605; Details auf Abruf, Qualität nur als Abweichung, fester Kopf (#625))
- Beleg: `tracks.html:382-408`, `tracks.html:420-442`, `tracks.html:487`, `base.html:414`,
  `tracks.html:866`, `base.html:907-911`
- Beobachtung: Auf einen Bildschirm passen wenige Tracks. `.sticky-col` hat keinen Versatz und
  klebt nicht, der Tabellenkopf scrollt weg. Jede Zeile trägt ein eigenes `<audio>`-Bedienfeld;
  Sortieren oder Blättern beendet die Wiedergabe. Der Toast überdeckt die Player-Leiste.
- Vorschlag: eine Zeile je Track; Pfad, Format und Plays in einen Detailbereich; Qualität und
  Status nur als Abweichung; ein `<audio>` in der Leiste, in der Zeile nur ein Abspielknopf;
  fester Tabellenkopf; Toast über der Leiste.
- Aufwand: M. Nutzen: Handgriff 2 und 4.

**8. Der Digest lässt sich nicht durchhören**
- Beleg: `digest.html:307-310`, `digest.html:244-246`
- Beobachtung: Der größte Bruch im häufigsten Handgriff: Jeder Vorschlag öffnet SoundCloud in
  einem neuen Tab, bewertet wird danach im alten.
- Vorschlag: Abspielknopf je Zeile und dieselbe Player-Leiste. Für Vorschläge in der Inbox die
  lokale Datei, sonst ein Schnipsel vom Server; `preview.extract_preview_from_url` liefert ihn
  heute schon für den Bot.
- Aufwand: L (neue Route, Zwischenspeicher). Nutzen: Handgriff 1, mit Befund 1 der größte Gewinn.

**9. Kaufliste, Wächter und Duplikate enden in Sackgassen** (Key-Pille, gesperrte Knöpfe und Ladehinweis der Inbox-Dubletten umgesetzt in #602)
- Beleg: `kaufliste.html:185-203`, `kaufliste.html:84-88`, `duplicates.html:53-56`,
  `duplicates.html:109`, `duplicates.html:275`, `duplicates.html:293-295`
- Beobachtung: „Jetzt verfügbar“ hat keinen Link. Ein Kauf lässt sich nicht abhaken.
  Fingerprint-Gruppen sind reine Anzeige ohne Vorhören. Die Key-Pille bleibt grau (`data-key` statt
  `data-camelot`). Nach einem Fehler beim Verschieben bleiben die Gruppen-Knöpfe gesperrt.
- Vorschlag: Link am Wächter-Eintrag; Zustand „erledigt“ je Kauf und Gate; in Duplikat-Gruppen
  Vorhören und „geprüft, ausblenden“; die zwei Fehler beheben.
- Aufwand: S für Link und Fehler, M bis L für die Zustände. Nutzen: Handgriff 3.

**10. Schmale Ansicht: Karten ohne Beschriftung, klebender Kopf**
- Beleg: `base.html:945-954`, `tracks.html:481-501`, `status.html:415-425`, `base.html:79-93`
- Beobachtung: Am Tablet im Hochformat braucht ein Track 13 Zeilen, vier davon ohne Label; sortieren
  geht nicht mehr. Der Kopf bleibt mehrzeilig stehen.
- Vorschlag: `data-label` überall nachtragen (S). Danach zweizeilige Listeneinträge, Kopf mit
  Seitentitel und Menü, Sortierung als Auswahlfeld (M).
- Aufwand: S, dann M. Nutzen: alle Handgriffe am Tablet; am Desktop keiner.

## Mockups

Beide Dateien sind eigenständiges HTML mit Inline-CSS, ohne Skripte und ohne externe Dateien.
Farben, Radien und Schrift-Stacks stammen aus `base.html:10-42`, die Camelot-Farben aus
`base.html:351-375`. Jede Datei zeigt die Fläche bei 1440 px und bei 420 px. Die schmale Ansicht
ist über eine Klasse am Rahmen gebaut; in der App wären das `@media`-Regeln.

**[`mockups/tracks-arbeitsflaeche.html`](mockups/tracks-arbeitsflaeche.html)** zeigt den
Track-Explorer als eine Fläche statt drei Karten.
- Zwei feste Zeilen für Suche, Sichten mit Anzahl und aktive Filter als entfernbare Chips;
  weitere Filter und die Spaltenwahl klappen per `<details>` auf (Befund 7).
- Tabelle mit 35 px Zeilenhöhe und festem Kopf; Energie als fünf Segmente, „Hinweise“ zeigt nur
  Abweichungen (neu, Clipped, übernommen) (Befund 4, 7).
- Markierte Zeile und laufende Zeile sind getrennte Zustände mit Balken links; Details der
  markierten Zeile stehen rechts (Befund 2, 7).
- Eine Player-Leiste mit Struktur des Tracks, Drop-Sprung, Bewertung und Tastaturhilfe; der
  Toast sitzt darüber und bietet „Rückgängig“ (Befund 1, 7).
- Alarm-Zähler im Kopf, Datenstand neben den Sichten (Befund 5).
- Schmal: zweizeilige Einträge, markierter Eintrag klappt auf (Befund 10).
- Darunter vier Zustände: keine Treffer, lädt, Fehler, Inbox abgearbeitet.

**[`mockups/digging-cockpit.html`](mockups/digging-cockpit.html)** ist ein neuer Einstieg mit
drei Zonen in Arbeitsrichtung.
- „Neu reingekommen“: die unbewerteten Vorschläge des Tages mit Abspielknopf und Bewertung;
  Bewertetes wandert nach unten, ein Balken zeigt den Fortschritt (Befund 1, 3, 8).
- „In der Inbox“: Verteilung nach Stil und Energie, darunter die besten Kandidaten nach Taste
  mit BPM und Key.
- „Besorgen & abhaken“: Wächter-Treffer, Gates und Käufe als Liste mit Kontrollkästchen und
  Link, dazu die Ablage für geladene Dateien (Befund 9).
- Statuszeile mit Alarm, Alter des Laufs und Workstation in Ortszeit (Befund 5).
- Schmal: die Zonen werden zu drei Reitern mit Zähler (Befund 10).

Annahmen, die über die heutigen Templates hinausgehen: Struktur und Drop je Track (`cues.py`,
`structure.py`), Subgenre (`style.py`), Label, Vorhören nicht geladener Vorschläge (Befund 8), der
Zustand „erledigt“ (Befund 9), „neu seit dem letzten Besuch“ und Anzahlen an den Sichten. Die
Statusseite bleibt als Betriebsansicht bestehen; das Cockpit ersetzt sie nur als Einstieg. Geprüft
sind die Mockups per DOM-Messung im Browser (kein Überlauf, Popover im Rahmen, Kopf klebt), nicht
per Screenshot.

## Nicht empfohlen

- **SoundCloud-Player einbetten.** Braucht Internet und fremde Skripte in der Seite. Befund 8 schlägt
  deshalb den Schnipsel vom eigenen Server vor.
- **JS-Framework, Build-Schritt oder virtuelles Scrollen.** Bei höchstens 200 Zeilen je Seite reicht
  schlichtes HTML; Dichte entsteht durch weniger Inhalt je Zeile.
- **Web-Fonts ausliefern.** `base.html:40-41` nennt Inter und JetBrains Mono, liefert sie aber nicht
  aus; der System-Stack genügt und hält die Seite ohne Netz vollständig.
- **Wellenform im Browser zeichnen.** WAV-Dateien clientseitig zu dekodieren ist langsam; der
  Struktur-Balken aus der vorhandenen Analyse beantwortet die DJ-Frage „wo ist der Drop“.
- **Set per Drag & Drop umsortieren.** Braucht Zustand im Browser und eine eigene Speicherung; der
  Set-Builder rechnet die Reihenfolge, Feinarbeit bleibt in Rekordbox.
- **Aktionen an der Sammlung** (Duplikate dort löschen oder verschieben): Goldene Regel 4.
- **Gates automatisch durchlaufen:** Goldene Regel 6. Das Cockpit verlinkt und hakt ab.
- **WebSocket oder Server-Sent Events** für die Statusseite: Ein Abruf pro Minute genügt.
- **Helles Theme, Theme-Umschalter, Telefon-Layout als Hauptziel.** Ein Nutzer, Desktop und Tablet.
- **Die Statusseite ins Cockpit einschmelzen.** Läufe, Betrieb und Job-Queue sind Diagnose, keine
  tägliche Arbeit.
