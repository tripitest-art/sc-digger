"""Cloud-Downloads: Dropbox und Google Drive sicher und ohne Datenreste laden."""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import requests

from .models import Track

log = logging.getLogger("sc_digger")

AUDIO_EXTS = (".flac", ".wav", ".aiff", ".aif", ".m4a", ".mp3")
ALLOWED_FINAL_HOSTS = ("dropbox.com", "dropboxusercontent.com", "google.com", "googleusercontent.com")


def direct_url(link: str) -> str | None:
    """Direkter Download-Link oder None (dann: nicht automatisch ladbar).

    - Host dropbox.com oder *.dropbox.com: Schema immer https, Host und Pfad unverändert,
      Query-Parameter in Reihenfolge behalten, dl auf 1 setzen bzw. dl=1 anhängen.
    - Host drive.google.com mit /file/d/<ID>/..., /open?id=<ID> oder /uc?id=<ID>:
      "https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t"
      (<ID> = [A-Za-z0-9_-]{10,}).
    - Alles andere -> None: Drive-Ordner, Mega (clientseitig verschlüsselt), WeTransfer
      (Links laufen ab), fremde Hosts, kaputte URLs. Wirft nie.
    """
    try:
        if not link:
            return None
        u = urlsplit(link.strip())
        if u.scheme.lower() not in ("http", "https"):
            return None
        host = (u.hostname or "").lower()
        if not host:
            return None

        # Dropbox
        if host == "dropbox.com" or host.endswith(".dropbox.com"):
            query_pairs = parse_qsl(u.query, keep_blank_values=True)
            has_dl = False
            new_pairs = []
            for k, v in query_pairs:
                if k == "dl":
                    new_pairs.append((k, "1"))
                    has_dl = True
                else:
                    new_pairs.append((k, v))
            if not has_dl:
                new_pairs.append(("dl", "1"))
            return urlunsplit(("https", u.netloc, u.path, urlencode(new_pairs), ""))

        # Google Drive
        if host == "drive.google.com":
            file_id: str | None = None
            m_file = re.match(r"^/file/d/([A-Za-z0-9_-]{10,})(?:/.*)?$", u.path)
            if m_file:
                file_id = m_file.group(1)
            elif u.path in ("/open", "/uc"):
                query_dict = dict(parse_qsl(u.query, keep_blank_values=True))
                cand_id = query_dict.get("id", "")
                if re.fullmatch(r"[A-Za-z0-9_-]{10,}", cand_id):
                    file_id = cand_id

            if file_id:
                return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            return None

        return None
    except Exception:
        return None


def _extract_filename(disposition: str) -> str:
    if not disposition:
        return ""
    m_star = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;\s]+)", disposition, re.IGNORECASE)
    if m_star:
        raw = unquote(m_star.group(1))
        return raw.replace("\\", "/").split("/")[-1].strip()
    m = re.search(r'''filename\s*=\s*(?:"([^"]+)"|'([^']+)'|([^;\s]+))''', disposition)
    if m:
        raw = m.group(1) or m.group(2) or m.group(3) or ""
        return raw.replace("\\", "/").split("/")[-1].strip()
    return ""


def _filename_from_url(resp_url: str | None, orig_url: str | None) -> str:
    for u in (resp_url, orig_url):
        if not u:
            continue
        p = urlsplit(u).path
        cand = unquote(p.split("/")[-1]) if p else ""
        cand = cand.replace("\\", "/").split("/")[-1].strip()
        if cand and cand not in (".", "..") and Path(cand).suffix.lower() in AUDIO_EXTS:
            return cand
    for u in (resp_url, orig_url):
        if not u:
            continue
        p = urlsplit(u).path
        cand = unquote(p.split("/")[-1]) if p else ""
        cand = cand.replace("\\", "/").split("/")[-1].strip()
        if cand and cand not in (".", ".."):
            return cand
    return ""


def _resolve_inbox_path(inbox: Path, name: str) -> Path:
    base = inbox / name
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    i = 1
    while True:
        cand = inbox / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def download_cloud(t: Track, inbox: Path, *, max_mb: int = 500,
                   session: requests.Session | None = None,
                   timeout: tuple[float, float] = (10, 60)) -> Path | None:
    """Lädt t.download_link, legt genau EINE Audiodatei direkt in inbox ab und gibt ihren Pfad
    zurück. Bei jedem Misserfolg: t.download_error = "<Grund auf Deutsch>", log.warning, None.
    Wirft nie. Hinterlässt nie Temp-Dateien, Teil-Downloads oder ZIPs (auch nicht im Fehlerfall).

    Ablauf:
    1. direct_url(t.download_link) ist None -> Fehler mit dem Wort "manuell", KEIN Request.
    2. inbox anlegen (mkdir parents). Arbeiten in einem eigenen Temp-Ordner in der inbox,
       der im finally immer gelöscht wird.
    3. resp = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
       (session: übergeben oder requests.Session()). Genutzt werden nur resp.status_code,
       resp.headers, resp.url, resp.iter_content(chunk_size=...), resp.close().
       requests.RequestException -> Fehler.
    4. Prüfen, in dieser Reihenfolge, jeweils Fehler:
       status_code >= 400 (Text enthält die Zahl, z. B. "HTTP 404");
       Host von resp.url nicht gleich/Subdomain eines ALLOWED_FINAL_HOSTS;
       Content-Type beginnt mit text/html (Text enthält "Webseite");
       Content-Length > max_mb MB (Text enthält "MB").
    5. Streamen in eine Temp-Datei; mehr als max_mb MB -> Fehler (Text enthält "MB").
    6. Dateiname: Content-Disposition filename* (UTF-8, url-dekodiert) vor filename, sonst
       letzter Pfadteil von resp.url bzw. url. Immer nur der letzte Teil nach / oder \\
       (kein Ausbruch aus der inbox).
    7. ZIP (erkannt per zipfile.is_zipfile, nicht per Endung): Summe der entpackten Größen
       > max_mb -> Fehler. Kandidaten: Dateien mit Endung in AUDIO_EXTS, ohne __MACOSX/ und
       ohne Namen, die mit "._" beginnen. Keiner -> Fehler (Text enthält "Audio").
       Auswahl: verlustfrei (.flac .wav .aiff .aif) vor .m4a vor .mp3, dann die größte.
       Bei mehreren Kandidaten Notiz in t.notes. Nur den letzten Namensteil verwenden.
    8. Kein ZIP und Endung nicht in AUDIO_EXTS -> Fehler (Text enthält "Audio").
    9. Zielname in der inbox belegt -> "<stem>_1<ext>", "_2", … (nie überschreiben).
    """
    t.download_error = None
    url = direct_url(t.download_link) if t.download_link else None
    if not url:
        t.download_error = "Cloud-Dienst wird nicht unterstützt (bitte manuell laden)"
        log.warning("Kein automatischer Cloud-Download für %s möglich: %s", t.url, t.download_link)
        return None

    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="_dl_", dir=inbox))

    sess = session or requests.Session()
    max_bytes = max_mb * 1024 * 1024

    try:
        try:
            resp = sess.get(url, stream=True, timeout=timeout, allow_redirects=True)
        except requests.RequestException as e:
            t.download_error = f"Netzwerkfehler beim Cloud-Download: {e}"
            log.warning("Netzwerkfehler beim Cloud-Download für %s (%s): %s", t.url, url, e)
            return None
        except Exception as e:
            t.download_error = f"Cloud-Download fehlgeschlagen: {e}"
            log.warning("Fehler beim Cloud-Download für %s (%s): %s", t.url, url, e)
            return None

        try:
            # 4. Prüfen, in dieser Reihenfolge:
            if resp.status_code >= 400:
                t.download_error = f"Cloud-Download fehlgeschlagen: HTTP {resp.status_code}"
                log.warning("HTTP %s für %s (%s)", resp.status_code, t.url, url)
                return None

            resp_host = (urlsplit(resp.url).hostname or "").lower()
            if not any(resp_host == h or resp_host.endswith("." + h) for h in ALLOWED_FINAL_HOSTS):
                t.download_error = f"Umleitung auf nicht erlaubten Host: {resp_host}"
                log.warning("Umleitung auf fremden Host %s verweigert für %s", resp_host, t.url)
                return None

            content_type = resp.headers.get("Content-Type", "").strip().lower()
            if content_type.startswith("text/html"):
                t.download_error = "Antwort ist eine Webseite (HTML) statt einer Audiodatei"
                log.warning("Antwort für %s ist HTML", url)
                return None

            cl_header = resp.headers.get("Content-Length")
            if cl_header:
                try:
                    cl = int(cl_header)
                    if cl > max_bytes:
                        t.download_error = f"Datei zu groß ({cl / (1024 * 1024):.1f} MB > {max_mb} MB)"
                        log.warning("Datei zu groß für %s: %s Bytes", url, cl)
                        return None
                except (ValueError, TypeError):
                    pass

            # 5. Streamen in eine Temp-Datei
            temp_file = temp_dir / "download.tmp"
            downloaded = 0
            with open(temp_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            t.download_error = f"Download überschreitet Limit von {max_mb} MB"
                            log.warning("Download überschreitet %s MB für %s", max_mb, url)
                            return None
                        f.write(chunk)

            # 6. Dateiname
            name = _extract_filename(resp.headers.get("Content-Disposition", ""))
            if not name or name in (".", ".."):
                name = _filename_from_url(resp.url, url)
            if not name or name in (".", ".."):
                name = "download"
            name = name.replace("\\", "/").split("/")[-1].strip()
            if not name or name in (".", ".."):
                name = "download"

            # 7. ZIP
            if zipfile.is_zipfile(temp_file):
                with zipfile.ZipFile(temp_file, "r") as zf:
                    total_uncompressed = sum(info.file_size for info in zf.infolist())
                    if total_uncompressed > max_bytes:
                        t.download_error = f"Entpackte ZIP-Größe überschreitet Limit von {max_mb} MB"
                        log.warning("ZIP entpackt zu groß für %s: %s Bytes", url, total_uncompressed)
                        return None

                    candidates: list[zipfile.ZipInfo] = []
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        clean_member = info.filename.replace("\\", "/").split("/")[-1].strip()
                        if "__MACOSX/" in info.filename or clean_member.startswith("._"):
                            continue
                        ext = Path(clean_member).suffix.lower()
                        if ext in AUDIO_EXTS:
                            candidates.append(info)

                    if not candidates:
                        t.download_error = "Keine unterstützte Audiodatei im ZIP gefunden"
                        log.warning("Keine Audiodatei im ZIP für %s", url)
                        return None

                    def _rank(item: zipfile.ZipInfo):
                        member_name = item.filename.replace("\\", "/").split("/")[-1].strip()
                        e = Path(member_name).suffix.lower()
                        if e in (".flac", ".wav", ".aiff", ".aif"):
                            prio = 0
                        elif e == ".m4a":
                            prio = 1
                        elif e == ".mp3":
                            prio = 2
                        else:
                            prio = 3
                        return (prio, -item.file_size)

                    candidates.sort(key=_rank)
                    best = candidates[0]
                    chosen_member_name = best.filename.replace("\\", "/").split("/")[-1].strip()
                    if len(candidates) > 1:
                        t.notes.append(f"ZIP enthielt {len(candidates)} Audiodateien, '{chosen_member_name}' gewählt")

                    target_path = _resolve_inbox_path(inbox, chosen_member_name)
                    target_path.write_bytes(zf.read(best))
                    log.info("Cloud-Download (ZIP) erfolgreich: %s -> %s", t.title, target_path.name)
                    return target_path

            # 8. Kein ZIP
            ext = Path(name).suffix.lower()
            if ext not in AUDIO_EXTS:
                t.download_error = f"Datei ist keine unterstützte Audiodatei ({ext or 'ohne Endung'})"
                log.warning("Keine Audiodatei für %s: %s", url, name)
                return None

            target_path = _resolve_inbox_path(inbox, name)
            shutil.move(str(temp_file), str(target_path))
            log.info("Cloud-Download erfolgreich: %s -> %s", t.title, target_path.name)
            return target_path

        finally:
            resp.close()

    except Exception as e:
        t.download_error = f"Unerwarteter Fehler beim Cloud-Download: {e}"
        log.warning("Unerwarteter Fehler beim Cloud-Download für %s: %s", t.url, e)
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
