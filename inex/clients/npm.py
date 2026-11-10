import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

REGISTRY_BASE = "https://registry.npmjs.org"
HTTP_TIMEOUT_SECONDS = 30
HTTP_OK = 200


class NpmClient:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._session = None

    def _cache_file(self, name):
        return self.cache_dir / f"{name.replace('/', '__')}.json"

    def package_document(self, name: str) -> dict | None:
        cached = self._cache_file(name)
        if cached.exists():
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Ignoring unreadable npm cache file %s", cached)
        if self._session is None:
            self._session = requests.Session()
        url = f"{REGISTRY_BASE}/{name.replace('/', '%2F')}"
        try:
            response = self._session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as error:
            logger.warning("npm registry request for %s failed: %s", name, error)
            return None
        if response.status_code != HTTP_OK:
            return None
        document = response.json()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(document), encoding="utf-8")
        return document
