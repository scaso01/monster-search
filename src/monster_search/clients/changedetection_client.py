"""changedetection.io website monitoring client."""

from __future__ import annotations


from monster_search.clients._pool import get_client
from monster_search.config import Config


class ChangeDetectionClient:
    """Client for changedetection.io URL monitoring."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _headers(self) -> dict[str, str]:
        api_key = self._config.changedetection_api_key
        if not api_key:
            raise ValueError(
                "changedetection.io API key required. Set MONSTER_CHANGEDETECTION_API_KEY "
                "env var with your API key from the web UI Settings > API."
            )
        return {"x-api-key": api_key}

    def _base_url(self) -> str:
        return f"{self._config.changedetection_url}/api/v1"

    def add_watch(self, url: str, *, tag: str | None = None) -> dict:
        """Add a new URL watch."""
        payload: dict = {"url": url}
        if tag:
            payload["tag"] = tag
        client = get_client(self._config.changedetection_url, 30)
        resp = client.post(
            f"{self._base_url()}/watch",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def list_watches(self, *, tag: str | None = None) -> list[dict]:
        """List all watches, optionally filtered by tag."""
        client = get_client(self._config.changedetection_url, 30)
        resp = client.get(
            f"{self._base_url()}/watch",
            headers=self._headers(),
        )
        resp.raise_for_status()
        watches = resp.json()
        # API returns dict of {uuid: watch_data}
        result = []
        for uuid, data in watches.items():
            entry = {"uuid": uuid, **data} if isinstance(data, dict) else {"uuid": uuid}
            if tag and entry.get("tag") != tag:
                continue
            result.append(entry)
        return result

    def get_latest(self, uuid: str) -> str:
        """Get the latest snapshot text for a watch."""
        client = get_client(self._config.changedetection_url, 30)
        resp = client.get(
            f"{self._base_url()}/watch/{uuid}/history/latest",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.text

    def get_diff(self, uuid: str) -> str:
        """Get the latest diff for a watch."""
        client = get_client(self._config.changedetection_url, 30)
        resp = client.get(
            f"{self._base_url()}/watch/{uuid}/diff/latest",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.text

    def remove_watch(self, uuid: str) -> bool:
        """Remove a watch by UUID."""
        client = get_client(self._config.changedetection_url, 30)
        resp = client.delete(
            f"{self._base_url()}/watch/{uuid}",
            headers=self._headers(),
        )
        return resp.status_code == 204 or resp.is_success
