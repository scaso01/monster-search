"""Who-Dat WHOIS lookup client."""

from __future__ import annotations


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class WhoDatClient:
    """Client for Who-Dat self-hosted WHOIS service."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(self, data: dict, query: str) -> list[SearchResult]:
        # Who-Dat may nest under "domain" and "registrar" keys
        domain_data = data.get("domain", data)
        registrar_data = data.get("registrar", data.get("registrar", {}))

        domain = domain_data.get("domain", domain_data.get("domain_name", query))
        if isinstance(registrar_data, dict):
            registrar = registrar_data.get("name", str(registrar_data))
        else:
            registrar = str(registrar_data) if registrar_data else "Unknown"
        creation_date = domain_data.get("created_date", domain_data.get("creation_date", "Unknown"))
        expiry_date = domain_data.get("expiration_date", domain_data.get("expiry_date", "Unknown"))
        name_servers = domain_data.get("name_servers", [])
        status = domain_data.get("status", [])

        ns_str = ", ".join(name_servers[:3]) if name_servers else "N/A"
        status_str = ", ".join(status[:3]) if status else "N/A"

        snippet = (
            f"Registrar: {registrar}\n"
            f"Created: {creation_date} | Expires: {expiry_date}\n"
            f"Name Servers: {ns_str}\n"
            f"Status: {status_str}"
        )

        return [
            SearchResult(
                title=domain,
                url=f"https://who.is/whois/{query}",
                snippet=snippet,
                source="whodat",
            )
        ]

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous WHOIS lookup for a domain."""
        client = get_client(self._config.whodat_url, self._config.whodat_timeout)
        resp = client.get(f"{self._config.whodat_url}/{query}")
        resp.raise_for_status()
        return self._parse_results(resp.json(), query)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async WHOIS lookup for a domain."""
        client = get_async_client(self._config.whodat_url, self._config.whodat_timeout)
        resp = await client.get(f"{self._config.whodat_url}/{query}")
        resp.raise_for_status()
        return self._parse_results(resp.json(), query)
