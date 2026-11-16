"""Genre-Profile aus der Konfiguration (Issue #565).

Ein Profil beschreibt eine Sicht auf die Track-DB: Name, BPM-Fenster, Tags und
Referenz-Accounts. Der Name ist der Wert von ``profile_id`` in der Track-DB
(``TrackDB(pfad, profile_id=profil.name)``). Ohne ``profiles:``-Block gibt es genau das
Standardprofil ``schranz``, das die Werte aus ``search`` übernimmt – eine Konfiguration ohne
den Block verhält sich also wie bisher.

Reine Logik ohne Datenbank und Netz. Den Schalter, der ein Profil für einen Lauf auswählt,
gibt es noch nicht.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any, Mapping

# Muss zu ``db.DEFAULT_PROFILE`` und zum DEFAULT der Spalten ``profile_id`` passen, sonst
# fänden Bestandsdaten ihr Profil nicht. Bewusst nicht aus ``db`` importiert: dieses Modul
# soll ohne sqlite ladbar bleiben; ``tests/test_profiles.py`` prüft die Gleichheit.
DEFAULT_PROFILE_NAME = "schranz"

# Rückfall, wenn die Konfiguration kein BPM-Fenster nennt (z. B. ``load_profiles({})``):
# die Werte aus der mitgelieferten ``config.yaml``.
_FALLBACK_BPM_MIN = 150.0
_FALLBACK_BPM_MAX = 165.0


@dataclass(frozen=True)
class Profile:
    name: str
    bpm_min: float
    bpm_max: float
    tags: tuple[str, ...] = ()
    reference_accounts: tuple[str, ...] = ()
    default_genre: str = "Schranz"


class ProfileError(ValueError):
    """Unbekanntes Profil oder ungültiger Block; die Meldung nennt den Namen."""


# Schlüssel, die ein Profil-Block setzen darf: alle Felder außer dem Namen (der ist der
# Schlüssel des Blocks selbst).
_BLOCK_KEYS = tuple(f.name for f in fields(Profile) if f.name != "name")


def _bpm(name: str, key: str, value: Any) -> float:
    # bool ist in Python ein int; "bpm_min: true" ist aber ein Tippfehler, keine 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(
            f"Profil {name!r}: {key} muss eine Zahl sein, nicht {type(value).__name__}."
        )
    if not math.isfinite(value) or value <= 0:
        raise ProfileError(f"Profil {name!r}: {key} muss größer als 0 sein, nicht {value!r}.")
    return float(value)


def _strings(name: str, key: str, value: Any) -> tuple[str, ...]:
    """Liste von Texten als Tupel. Ein einzelner Text ist bewusst keine Liste: ``tags: house``
    zerfiele sonst in einzelne Buchstaben."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ProfileError(
            f"Profil {name!r}: {key} muss eine Liste von Texten sein, "
            f"nicht {type(value).__name__}."
        )
    for eintrag in value:
        if not isinstance(eintrag, str):
            raise ProfileError(
                f"Profil {name!r}: {key} muss eine Liste von Texten sein, "
                f"enthält aber {type(eintrag).__name__}."
            )
    return tuple(value)


def _build(name: str, werte: Mapping[str, Any]) -> Profile:
    """Prüft die (schon mit den Vorgaben gemischten) Werte und baut das Profil."""
    bpm_min = _bpm(name, "bpm_min", werte["bpm_min"])
    bpm_max = _bpm(name, "bpm_max", werte["bpm_max"])
    if bpm_min > bpm_max:
        raise ProfileError(
            f"Profil {name!r}: bpm_min ({bpm_min:g}) ist größer als bpm_max ({bpm_max:g})."
        )
    genre = werte["default_genre"]
    if not isinstance(genre, str) or not genre.strip():
        raise ProfileError(f"Profil {name!r}: default_genre muss ein nicht leerer Text sein.")
    return Profile(
        name=name,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
        tags=_strings(name, "tags", werte["tags"]),
        reference_accounts=_strings(name, "reference_accounts", werte["reference_accounts"]),
        default_genre=genre,
    )


def load_profiles(cfg: Mapping[str, Any]) -> dict[str, Profile]:
    """Liest `profiles:` aus der Konfiguration. Vorgaben kommen aus `search.bpm_min`,
    `search.bpm_max`, `search.tags`, `search.reference_accounts`; ohne `profiles:`-Block
    existiert genau `DEFAULT_PROFILE_NAME` und erbt alle Vorgaben.

    Ein Profil-Block überschreibt nur die Schlüssel, die er nennt; ein Wert ``None`` (auch
    ein ganz leerer Block) gilt als „nicht gesetzt“. Das Standardprofil existiert immer und
    darf im Block selbst überschrieben werden. Unbekannter Schlüssel, falscher Typ oder
    ``bpm_min > bpm_max`` ⇒ ProfileError mit dem Profilnamen in der Meldung.
    """
    search = cfg.get("search") or {}
    if not isinstance(search, Mapping):
        raise ProfileError(
            f"Profil {DEFAULT_PROFILE_NAME!r}: search muss ein Abschnitt sein, "
            f"nicht {type(search).__name__}."
        )

    def vorgabe(key: str, rueckfall: Any) -> Any:
        wert = search.get(key)
        return rueckfall if wert is None else wert

    vorgaben: dict[str, Any] = {
        "bpm_min": vorgabe("bpm_min", _FALLBACK_BPM_MIN),
        "bpm_max": vorgabe("bpm_max", _FALLBACK_BPM_MAX),
        "tags": vorgabe("tags", ()),
        "reference_accounts": vorgabe("reference_accounts", ()),
        "default_genre": Profile.__dataclass_fields__["default_genre"].default,
    }

    bloecke = cfg.get("profiles")
    if bloecke is None:
        bloecke = {}
    if not isinstance(bloecke, Mapping):
        raise ProfileError(
            f"profiles: Abschnitt mit einem Block je Profil erwartet, "
            f"nicht {type(bloecke).__name__}."
        )

    # Das Standardprofil steht immer vorn, auch wenn der Block es nicht nennt.
    roh: dict[Any, Any] = {DEFAULT_PROFILE_NAME: None}
    roh.update(bloecke)

    profile: dict[str, Profile] = {}
    for name, block in roh.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            # Der Name wird zur profile_id in der Track-DB; Zahlen (YAML-Schlüssel "140:")
            # oder Leerraum am Rand fände später niemand wieder.
            raise ProfileError(
                f"Profil {name!r}: der Name muss ein nicht leerer Text ohne Leerraum am Rand sein."
            )
        if block is None:
            block = {}
        if not isinstance(block, Mapping):
            raise ProfileError(
                f"Profil {name!r}: Block mit Schlüsseln erwartet, nicht {type(block).__name__}."
            )
        werte = dict(vorgaben)
        for key, value in block.items():
            if key not in _BLOCK_KEYS:
                raise ProfileError(
                    f"Profil {name!r}: unbekannter Schlüssel {key!r} "
                    f"(erlaubt: {', '.join(_BLOCK_KEYS)})."
                )
            if value is not None:
                werte[key] = value
        profile[name] = _build(name, werte)
    return profile


def get_profile(cfg: Mapping[str, Any], name: str) -> Profile:
    """Profil per Namen; unbekannt ⇒ ProfileError mit dem Namen in der Meldung."""
    profile = load_profiles(cfg)
    try:
        return profile[name]
    except (KeyError, TypeError):
        raise ProfileError(
            f"Unbekanntes Profil {name!r} (vorhanden: {', '.join(sorted(profile))})."
        ) from None
