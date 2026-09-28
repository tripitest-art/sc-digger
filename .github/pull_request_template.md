## Was und warum

<!-- Welches Problem löst dieser PR? Issue verlinken: "Closes #12" -->

## Wie getestet

- [ ] `python -m pytest -q` grün
- [ ] Neue Logik / behobener Fehler hat einen Test
- [ ] Bei Änderungen am Lauf: `--dry-run --no-telegram -v` einmal ausgeführt

## Checkliste (AGENTS.md)

- [ ] Ein Thema, kleiner Umfang
- [ ] Sammlung (`/music/Schranz`) wird nicht verändert
- [ ] Keine Zugangsdaten in Code, Logs oder Fehlermeldungen (Telegram nur über `telegram_call`)
- [ ] `config.yaml`: neue Optionen haben einen Standardwert und einen Kommentar
- [ ] Doku angepasst (README / AGENTS.md / ROADMAP), falls sich Verhalten oder Betrieb ändert

## Umgesetzt von / Review durch

<!-- z. B. "Claude (Opus) / Review: Gemini" – Review nie vom selben Agenten -->
