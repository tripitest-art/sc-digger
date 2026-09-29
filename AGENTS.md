# AGENTS.md – Einweisung für alle Agenten (Claude, Gemini, Codex, …)

## 📄 Version: 2024.1219-00 | Status: Stable | Author: Admin | Team: Stephan + Review Board
## Generated: 2024-12-19T00:00Z

### ⚠️ Goldene Regeln

> Lies die gesamte Datei, bevor du etwas änderst. Abweichungen im PR begründen.

1. **GitHub main ist die einzige Wahrheit.** Nie Code direkt auf dem Server oder in lockeren Kopien ändern. Der Server holt nur (Deploy-Key ohne Schreibrecht).
2. **Ein Branch pro Aufgabe,** nach `main` nur per Pull Request. Branch-Namen: `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`. Vor dem Start `main` aktualisieren.
3. **Tests müssen grün sein.** `python -m pytest -q` vor jedem Push. Neue Logik bekommt Tests; behobene Fehler bekommen einen Test, der den Fehler reproduziert.
4. **Die Sammlung wird nie verändert.** `/music/Schranz` ist schreibgeschützt eingebunden und bleibt es. Rekordbox verknüpft Cues über Dateipfade; verschieben, umbenennen oder Audio bearbeiten zerstört sie. Sortieren und Taggen nur in der Inbox.
5. **Zugangsdaten nie in Repo, Logs, Fehlermeldungen oder Chat.** Telegram-API immer über `output.telegram_call()` (nie `requests` direkt, die URL enthält den Token). Logging über `redact.install_redacting_logging()`. Keine `.env`-Inhalte ausgeben, auch nicht „maskiert" – Namen und Längen reichen.
6. **Fair zu Artists.** Gates (Hypeddit, Droploud, …) werden erkannt und verlinkt, nie umgangen: keine Wegwerf-Adressen, keine Link-Leaks, keine Fake-Accounts. Stream-Rips werden nicht geladen (`scdl --only-original`).
7. **Nicht still scheitern.** Fehler, die den täglichen Lauf betreffen, müssen im Health-Alarm oder im Digest sichtbar werden.
8. **Nicht zwei Agenten in denselben Dateien.** `sc_digger/main.py` ist der Engpass (alle Modi laufen dort zusammen). Wer dort arbeitet, schreibt es ins Issue.
9. **Token-Effizienz:** Atomic PRs (eine Datei/Thema), JSON-Output, Mocking & Fakes aus Hauptcode auslagern, Delta-Context (nur neue Zeilen pushen).
10. **Versionierung:** Header mit Datum/Version/Status in AGENTS.md hinzufügen für CI-Tracking und Rollback.

### 🔍 Worum es geht

`sc-digger` ist ein Crate-Digging-Tool für Schranz/Hard Techno. Es sucht täglich neue SoundCloud-Tracks, bewertet sie genre-relativ, gleicht sie mit der Sammlung ab, lädt freigegebene Originale, prüft deren Qualität, analysiert BPM/Key, taggt und sortiert sie in eine Inbox und schickt einen Telegram-Digest. Ein Telegram-Bot prüft Playlists und Track-Stations auf Zuruf.

Details und Ausbaustufen: `ROADMAP.md`.