from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.arxiv import ArxivClient
from monster_search.config import Config
from monster_search.models import SearchResult

MOCK_ATOM_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: search_query=all:transformers</title>
  <id>http://arxiv.org/api/query</id>
  <opensearch:totalResults>1000</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>5</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T00:00:00Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All You Need</title>
    <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1810.04805v2</id>
    <updated>2019-05-24T00:00:00Z</updated>
    <published>2018-10-11T17:58:00Z</published>
    <title>BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding</title>
    <summary>We introduce a new language representation model called BERT, which stands for Bidirectional Encoder Representations from Transformers.</summary>
    <author><name>Jacob Devlin</name></author>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""

MOCK_EMPTY_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>ArXiv Query: search_query=all:nonexistent</title>
  <id>http://arxiv.org/api/query</id>
  <opensearch:totalResults>0</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>5</opensearch:itemsPerPage>
</feed>"""


@respx.mock
def test_arxiv_search():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=MOCK_ATOM_RESPONSE)
    )
    client = ArxivClient()
    results = client.search("transformers")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "Attention Is All You Need"
    assert results[0].source == "arxiv"
    assert results[0].url == "http://arxiv.org/abs/1706.03762v7"
    assert results[0].published == "2017-06-12T17:57:34Z"
    assert results[0].category == "cs.CL"


@respx.mock
def test_arxiv_search_max_results():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=MOCK_ATOM_RESPONSE)
    )
    client = ArxivClient()
    results = client.search("transformers", max_results=1)
    assert len(results) == 1


@respx.mock
def test_arxiv_search_custom_config():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=MOCK_ATOM_RESPONSE)
    )
    config = Config(arxiv_timeout=60)
    client = ArxivClient(config=config)
    results = client.search("transformers")
    assert len(results) == 2


@respx.mock
def test_arxiv_search_error():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(500)
    )
    client = ArxivClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("transformers")


@respx.mock
def test_arxiv_search_empty():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=MOCK_EMPTY_RESPONSE)
    )
    client = ArxivClient()
    results = client.search("nonexistent")
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_arxiv_async_search():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=MOCK_ATOM_RESPONSE)
    )
    client = ArxivClient()
    results = await client.asearch("transformers")
    assert len(results) == 2
    assert results[0].source == "arxiv"
    assert results[1].title == "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"


@respx.mock
def test_arxiv_sends_correct_params():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=MOCK_ATOM_RESPONSE)
    )
    client = ArxivClient()
    client.search("neural networks", max_results=10)
    request = respx.calls[0].request
    url_str = str(request.url)
    assert "search_query=all%3Aneural+networks" in url_str or "search_query=all%3Aneural%20networks" in url_str
    assert "max_results=10" in url_str
    assert "sortBy=relevance" in url_str


@respx.mock
def test_arxiv_snippet_truncation():
    long_summary = "B" * 600
    atom = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/0000.00000v1</id>
    <published>2024-01-01T00:00:00Z</published>
    <title>Long Summary Paper</title>
    <summary>{long_summary}</summary>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=atom)
    )
    client = ArxivClient()
    results = client.search("test")
    assert len(results[0].snippet) == 500
