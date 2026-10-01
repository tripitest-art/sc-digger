"""Zugangsdaten aus Log-Ausgaben, Fehlermeldungen und der Health-DB heraushalten.

Der Telegram-Bot-Token steckt in jeder API-URL (https://api.telegram.org/bot<TOKEN>/...).
requests schreibt diese URL in HTTPError-Meldungen, und damit landete der Token im
Container-Log, in der Health-DB und im Health-Alarm. Der SoundCloud-Token steckt in der
scdl-Kommandozeile und damit in subprocess-Fehlern (z. B. TimeoutExpired).
"""
from __future__ import annotations

import logging
import os
import re

_TG_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")
_SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "SOUNDCLOUD_AUTH_TOKEN")
# Token: mindestens 8 Zeichen aus [A-Za-z0-9._-] UND mindestens eine Ziffer.
# Die Ziffer verhindert Fehltreffer bei normalen Wörtern ("OAuth required").
_TOKEN_BODY = r"(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9._-]{8,}"
_OAUTH_HEADER_RE = re.compile(r"(OAuth\s+)" + _TOKEN_BODY + r"(?=[\s&]|$)", re.IGNORECASE)
_OAUTH_URL_PARAM_RE = re.compile(r"(oauth_token=)" + _TOKEN_BODY + r"(?=[\s&]|$)", re.IGNORECASE)
_AUTH_TOKEN_ARG_RE = re.compile(r"(--auth-token[=\s])" + _TOKEN_BODY + r"(?=[\s&]|$)", re.IGNORECASE)


def redact(text: object) -> str:
    """Ersetzt bekannte Zugangsdaten in einem beliebigen Text durch ***."""
    s = str(text)
    s = _TG_TOKEN_RE.sub("bot***", s)
    
    # Entferne OAuth-Tokens aus verschiedenen Stellen
    s = _OAUTH_HEADER_RE.sub(r"\1***", s)
    s = _OAUTH_URL_PARAM_RE.sub(r"\1***", s)
    s = _AUTH_TOKEN_ARG_RE.sub(r"\1***", s)
    
    for name in _SECRET_ENV:
        value = os.environ.get(name)
        if value and len(value) >= 8:
            s = s.replace(value, "***")
    return s


class RedactingFormatter(logging.Formatter):
    """Formatter, der die komplette Ausgabe inkl. Traceback bereinigt."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def install_redacting_logging(level: int) -> None:
    """logging.basicConfig-Ersatz: jede Log-Zeile läuft durch redact()."""
    logging.basicConfig(level=level)
    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(fmt)
