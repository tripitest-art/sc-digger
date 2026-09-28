# Anweisungen für Copilot Code Review

Die Regeln des Projekts stehen in `AGENTS.md` im Repo-Wurzelverzeichnis. Review-Kommentare
bitte auf Deutsch.

## Worauf achten

- **Korrektheit und Fehlerbehandlung:** Netzwerk- und Dateifehler dürfen den täglichen Lauf
  nicht still abbrechen; sie müssen im Health-Alarm oder Digest sichtbar werden.
- **Zugangsdaten:** Die Telegram-API nur über `output.telegram_call()`, nie `requests`
  direkt (der Token steckt in der URL und damit in Fehlermeldungen). Keine Tokens oder
  `.env`-Inhalte in Logs, Ausnahmen oder Tests.
- **Sammlung ist schreibgeschützt:** Code darf `/music/Schranz` nie verändern, verschieben
  oder umbenennen. Schreiben nur in der Inbox.
- **Gates werden nicht umgangen:** Hypeddit, Droploud usw. nur erkennen und verlinken.
- **Tests:** Netzwerk immer über Fakes. Auf Tests achten, die nichts prüfen (`assert True`,
  gemockter Prüfling, zu schwache Vergleiche).

## Was du nicht vorschlagen sollst

- Refactorings oder Änderungen außerhalb der Dateien, die der PR ohnehin ändert.
- Änderungen an `tests/acceptance/`, neue `skip`/`xfail`, Änderungen an `.github/`.
- Reine Stilfragen, die nicht im Widerspruch zu `AGENTS.md` stehen.
