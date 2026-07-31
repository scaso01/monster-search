"""PriceGhost self-hosted price tracker client.

Queries a PriceGhost instance for tracked products matching a search query.
PriceGhost tracks prices from Amazon, Best Buy, Walmart, Newegg, eBay,
Target, Costco, Home Depot, AliExpress, and more.

Since PriceGhost is a price *tracker* (not a search engine), this client
queries all tracked products and filters by keyword match against product
names.  Authentication is required (JWT).
"""

from __future__ import annotations

import logging

import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

logger = logging.getLogger(__name__)


class PriceGhostClient:
    """Client for a self-hosted PriceGhost price-tracking instance."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._base_url: str = self._config.priceghost_url
        self._timeout: int = self._config.priceghost_timeout
        self._email: str = self._config.priceghost_email
        self._password: str = self._config.priceghost_password
        self._token: str | None = None

    # -- auth helpers --------------------------------------------------------

    def _login_sync(self) -> str:
        """Authenticate synchronously and return a JWT token."""
        client = get_client(self._base_url, self._timeout)
        resp = client.post(
            f"{self._base_url}/api/auth/login",
            json={"email": self._email, "password": self._password},
        )
        resp.raise_for_status()
        token: str = resp.json().get("token", "")
        if not token:
            raise ValueError("PriceGhost login returned no token")
        return token

    async def _login_async(self) -> str:
        """Authenticate asynchronously and return a JWT token."""
        client = get_async_client(self._base_url, self._timeout)
        resp = await client.post(
            f"{self._base_url}/api/auth/login",
            json={"email": self._email, "password": self._password},
        )
        resp.raise_for_status()
        token: str = resp.json().get("token", "")
        if not token:
            raise ValueError("PriceGhost login returned no token")
        return token

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # -- result parsing ------------------------------------------------------

    @staticmethod
    def _parse_results(
        products: list[dict],
        query: str,
        max_results: int,
    ) -> list[SearchResult]:
        """Filter products by keyword and convert to SearchResult."""
        query_lower = query.lower()
        keywords = query_lower.split()

        results: list[SearchResult] = []
        for product in products:
            name = product.get("name") or ""
            name_lower = name.lower()

            # All keywords must appear in the product name
            if not all(kw in name_lower for kw in keywords):
                continue

            url = product.get("url", "")
            current_price = product.get("current_price")
            stock_status = product.get("stock_status", "unknown")

            # Build price string
            price_str: str | None = None
            if current_price is not None:
                try:
                    price_str = f"${float(current_price):.2f}"
                except (ValueError, TypeError):
                    price_str = str(current_price)

            in_stock: bool | None = None
            if stock_status == "in_stock":
                in_stock = True
            elif stock_status == "out_of_stock":
                in_stock = False

            # Build snippet
            parts: list[str] = []
            if price_str:
                parts.append(f"Price: {price_str}")
            if in_stock is True:
                parts.append("In Stock")
            elif in_stock is False:
                parts.append("Out of Stock")
            else:
                parts.append("Stock: Unknown")

            # Add price history stats if available
            stats = product.get("stats")
            if isinstance(stats, dict):
                min_price = stats.get("min_price")
                max_price = stats.get("max_price")
                if min_price is not None and max_price is not None:
                    try:
                        parts.append(
                            f"Range: ${float(min_price):.2f} - ${float(max_price):.2f}"
                        )
                    except (ValueError, TypeError):
                        pass

            snippet = " | ".join(parts) if parts else "Tracked by PriceGhost"

            results.append(
                SearchResult(
                    title=name,
                    url=url,
                    snippet=snippet,
                    source="priceghost",
                    price=price_str,
                    in_stock=in_stock,
                )
            )

            if len(results) >= max_results:
                break

        return results

    # -- public API ----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search of tracked PriceGhost products."""
        if not self._email or not self._password:
            logger.debug("PriceGhost credentials not configured, skipping")
            return []

        max_results = max_results or getattr(self._config, "max_results", 5)

        try:
            if not self._token:
                self._token = self._login_sync()

            client = get_client(self._base_url, self._timeout)
            resp = client.get(
                f"{self._base_url}/api/products",
                headers=self._auth_headers(self._token),
            )
            resp.raise_for_status()
            products = resp.json()
            if not isinstance(products, list):
                products = products.get("products", [])
            return self._parse_results(products, query, max_results)

        except httpx.HTTPStatusError as exc:
            # If 401, token may be expired -- clear it for next call
            if exc.response.status_code == 401:
                self._token = None
            logger.warning("PriceGhost HTTP error: %s", exc)
            return []
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("PriceGhost connection error: %s", exc)
            return []
        except Exception as exc:
            logger.warning("PriceGhost unexpected error: %s", exc)
            return []

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search of tracked PriceGhost products."""
        if not self._email or not self._password:
            logger.debug("PriceGhost credentials not configured, skipping")
            return []

        max_results = max_results or getattr(self._config, "max_results", 5)

        try:
            if not self._token:
                self._token = await self._login_async()

            client = get_async_client(self._base_url, self._timeout)
            resp = await client.get(
                f"{self._base_url}/api/products",
                headers=self._auth_headers(self._token),
            )
            resp.raise_for_status()
            products = resp.json()
            if not isinstance(products, list):
                products = products.get("products", [])
            return self._parse_results(products, query, max_results)

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self._token = None
            logger.warning("PriceGhost HTTP error: %s", exc)
            return []
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("PriceGhost connection error: %s", exc)
            return []
        except Exception as exc:
            logger.warning("PriceGhost unexpected error: %s", exc)
            return []
