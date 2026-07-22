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
from .session_agent_responsive import run_forever_responsive


FETCH_ATTEMPTS = 4
FETCH_TIMEOUT_SECONDS = 25


def _fetch_work_items_with_retry(
    settings: Settings,
    window: homepicz_scheduler.ImportWindow,
) -> list[homepicz_scheduler.WorkItem]:
    """Consulta detalhes dos trabalhos tolerando timeouts transitórios."""
    if not settings.homepicz_appscript_url:
        raise RuntimeError("Configure homepicz_appscript_url")

    params = (
        {"data": window.start.isoformat(), "detalhes": "1"}
        if window.start == window.end
        else {"inicio": window.start.isoformat(), "fim": window.end.isoformat(), "detalhes": "1"}
    )
    separator = "&" if "?" in settings.homepicz_appscript_url else "?"
    url = f"{settings.homepicz_appscript_url}{separator}{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "LRAutomatic/session-agent"},
    )

    last_error: BaseException | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
            items = homepicz_scheduler._parse_work_items(payload)
            if not items:
                raise RuntimeError("Apps Script respondeu sem trabalhos válidos")
            return items
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(min(6.0, 1.0 * (2**attempt)))
                continue
            break

    raise RuntimeError(
        f"Apps Script indisponível após {FETCH_ATTEMPTS} tentativas; "
        f"o ciclo será repetido automaticamente: {last_error}"
    )


homepicz_scheduler._fetch_work_items = _fetch_work_items_with_retry

# A atribuição acima substitui o fetcher. Reinstala o filtro por editor sobre a
# versão com retry para que "importar apenas pertencentes a você" continue valendo.
try:
    from .homepicz_editor_features import install_homepicz_editor_features

    install_homepicz_editor_features()
except Exception:
    pass


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.json")
    run_forever_responsive(config_path)


if __name__ == "__main__":
    main()
