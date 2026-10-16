# Sicherheitslücken melden

sc-digger ist ein öffentliches Repository und steuert autonome Agenten mit Schreibrecht:
Downloads, einen Telegram-Bot und Zugriff auf die Musiksammlung. Sicherheitslücken gehören
deshalb vertraulich gemeldet, nicht in die Öffentlichkeit.

## Meldeweg

Bitte nutze GitHubs **Private vulnerability reporting**: das Formular unter dem Pfad
`security/advisories/new` im Reiter „Security“ dieses Repositories. Dort beschreibst du die
Lücke vertraulich; nur Stephan sieht die Meldung.

Melde keine Lücke über ein öffentliches Issue, einen Pull Request, einen Kommentar oder
einen Chat. Ein öffentliches Issue macht die Lücke angreifbar, bevor sie behoben ist.

## Keine Zugangsdaten in der Öffentlichkeit

Schreibe niemals Zugangsdaten – Telegram-Token, SoundCloud-Zugang, API-Schlüssel – in
öffentliche Issues, PR-Texte, Kommentare oder Logs. Solche Werte bleiben ausschließlich in
der nicht versionierten `.env` auf dem Server (Regel 5 in `AGENTS.md`).

## Unterstützte Version

Unterstützt wird nur der Stand von `main`. Es gibt keine gepflegten Release-Zweige;
Fehlerbehebungen landen in `main` und werden über `./update.sh` auf den Server eingespielt.

## Was danach passiert

Stephan prüft die Meldung und antwortet im vertraulichen Meldeweg. Fristen oder Zusagen
gibt es nicht.
