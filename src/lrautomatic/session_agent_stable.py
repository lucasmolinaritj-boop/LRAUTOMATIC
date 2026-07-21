from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import homepicz_scheduler
from .config import Settings
from .session_agent import run_forever


FETCH_ATTEMPTS = 4
FETCH_TIMEOUT_SECONDS = 25


def _fetch_ids_with_retry(settings: Settings, window: homepicz_scheduler.ImportWindow) -> list[str]:
    """Consulta o Apps Script tolerando timeout transitório sem abortar o ciclo inteiro."""
    if not settings.homepicz_appscript_url:
        raise RuntimeError("Configure homepicz_appscript_url")
    if window.start == window.end:
        query = urllib.parse.urlencode({"data": window.start.isoformat()})
    else:
        query = urllib.parse.urlencode({"inicio": window.start.isoformat(), "fim": window.end.isoformat()})
    separator = "&" if "?" in settings.homepicz_appscript_url else "?"
    url = f"{settings.homepicz_appscript_url}{separator}{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "LRAutomatic/session-agent"},
    )

    last_error: BaseException | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
            ids = payload.get("ids") if isinstance(payload, dict) else None
            if not isinstance(ids, list):
                raise RuntimeError("Apps Script respondeu sem o campo ids")
            return list(dict.fromkeys(str(value).strip() for value in ids if str(value).strip()))
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(min(6.0, 1.0 * (2**attempt)))
                continue
            break

    raise RuntimeError(
        f"Apps Script indisponível após {FETCH_ATTEMPTS} tentativas; o ciclo será repetido automaticamente: {last_error}"
    )


# run_cycle consulta este símbolo no módulo homepicz_scheduler em tempo de execução.
homepicz_scheduler._fetch_ids = _fetch_ids_with_retry


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.json")
    run_forever(config_path)


if __name__ == "__main__":
    main()
