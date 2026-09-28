# Akzeptanztests

Jede Datei `test_issue_<N>.py` ist der 1:1 übernommene Akzeptanztest-Block aus Issue #N.

- **Nicht bearbeiten.** Der CI-Check `acceptance-guard` vergleicht die Datei mit dem Issue
  und blockiert Änderungen an bereits gemergten Akzeptanztests.
- Ist ein Akzeptanztest falsch, wird **das Issue** korrigiert (und die Datei neu übernommen),
  nie die Datei allein.
- Ablauf: `AGENTS.md`, Abschnitt „Worker-Aufgaben“.
