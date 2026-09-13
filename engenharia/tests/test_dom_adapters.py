"""
The two DOM adapters: veploy and microsistec_a (plan section 17).

No network. The HTML fragments below are SYNTHETIC reconstructions of the
markup shapes measured on live pages -- real captured pages are not committed,
for the reason at the top of test_privacy.py.

Both adapters share a structural problem the API adapters do not have: the
transaction is knowable only after the page is fetched, while `discover()` must
label a URL before yielding it. The tests here pin the resolution and, more
importantly, the cases where the obvious shortcut would have been wrong.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coleta.anuncios.adapters.base import AdapterError  # noqa: E402
from coleta.anuncios.adapters.microsistec_a import MicrosistecAAdapter  # noqa: E402
from coleta.anuncios.adapters.veploy import VeployAdapter, offers_by_transaction  # noqa: E402
from comum.config import Source  # noqa: E402
from comum.jsonld import extract_blocks  # noqa: E402
from comum.normalizer import Normalizer  # noqa: E402


class FakeSession:
    """Serves a sitemap and a page map; counts fetches."""

    def __init__(self, pages, sitemap_urls=None):
        self.pages = pages
        self.sitemap_urls = sitemap_urls if sitemap_urls is not None else list(pages)
        self.fetched = []

    def sitemap_candidates(self):
        return ["https://exemplo.com.br/sitemap.xml"]

    def iter_sitemap(self, url):
        return iter(self.sitemap_urls)

    def get_text(self, url, conditional=True):
        self.fetched.append(url)
        return self.pages.get(url)

    def get(self, url, **kw):
        return None

    def close(self):
        pass


# --------------------------------------------------------------------------
# veploy
# --------------------------------------------------------------------------

def veploy_page(sale=430000, rent=3000, name=None, condo="R$ 450,00",
                iptu="R$ 120,00"):
    name = name or ("Apartamento com 2 dormitórios, 51 m² - venda por "
                    "R$ 430.000,00 - Centro - Santos/SP")
    offers = []
    if sale:
        offers.append('{"@type":"Offer","price":%d,"priceCurrency":"BRL",'
                      '"businessFunction":"http://purl.org/goodrelations/v1#Sell"}' % sale)
    if rent:
        offers.append('{"@type":"Offer","price":%d,"priceCurrency":"BRL",'
                      '"businessFunction":"http://purl.org/goodrelations/v1#LeaseOut"}' % rent)
    blocks = "".join(
        f'<script type="application/ld+json">{o}</script>' for o in offers)
    residence = ('{"@type":"Apartment","name":"%s","numberOfBedrooms":2,'
                 '"numberOfBathroomsTotal":1,"numberOfParkingSpaces":1,'
                 '"floorSize":{"@type":"QuantitativeValue","value":60,"unitCode":"MTK"},'
                 '"amenityFeature":[{"@type":"LocationFeatureSpecification","name":"Piscina"}]}'
                 % name)
    money = ""
    if condo:
        money += f"<span><strong>Condomínio</strong> {condo}</span>"
    if iptu:
        money += f"<span><strong>IPTU</strong> {iptu}</span>"
    return (f'<html><script type="application/ld+json">{residence}</script>'
            f'{blocks}{money}'
            '<span class="text-xs uppercase">Suítes</span>'
            '<span class="font-bold text-gray-900">1</span>'
            '</html>')


VEPLOY_URL = "https://serlamimobiliaria.com.br/imovel/AP0054/apartamento-centro"


def veploy(pages, sitemap=None):
    src = Source(domain="serlamimobiliaria.com.br", platform="veploy")
    return VeployAdapter(FakeSession(pages, sitemap), src)


class TestVeployTransaction(unittest.TestCase):
    """Why businessFunction and not the URL slug."""

    def test_dual_listing_is_discovered_under_both(self):
        a = veploy({VEPLOY_URL: veploy_page(sale=430000, rent=3000)})
        self.assertEqual(len(list(a.discover(a.domain, "venda"))), 1)
        self.assertEqual(len(list(a.discover(a.domain, "locacao"))), 1)

    def test_each_row_gets_its_own_price(self):
        a = veploy({VEPLOY_URL: veploy_page(sale=430000, rent=3000)})
        prices = {}
        for t in ("venda", "locacao"):
            d = next(iter(a.discover(a.domain, t)))
            prices[t] = a.parse(a.fetch(d))["price"]
        self.assertEqual(prices, {"venda": 430000.0, "locacao": 3000.0})

    def test_sale_only_listing_is_not_a_rental(self):
        a = veploy({VEPLOY_URL: veploy_page(sale=430000, rent=None)})
        self.assertEqual(list(a.discover(a.domain, "locacao")), [])

    def test_url_slug_is_not_consulted(self):
        """
        61 of 1,455 live URLs carry no transaction token and 91 carry both.
        A slug-derived label would drop 4% and mislabel 6% -- silently.
        """
        url = "https://serlamimobiliaria.com.br/imovel/AP0039/apartamento-2-quartos-praia"
        a = veploy({url: veploy_page(sale=None, rent=2500)}, sitemap=[url])
        found = list(a.discover(a.domain, "locacao"))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].transaction_type, "locacao")

    def test_offer_without_business_function_is_ignored(self):
        page = ('<html><script type="application/ld+json">'
                '{"@type":"Offer","price":123456,"priceCurrency":"BRL"}</script></html>')
        a = veploy({VEPLOY_URL: page})
        self.assertEqual(list(a.discover(a.domain, "venda")), [])
        self.assertEqual(a.unlabelled, 1)

    def test_offers_by_transaction_helper(self):
        blocks = extract_blocks(veploy_page(sale=1, rent=2))
        self.assertEqual(offers_by_transaction(blocks),
                         {"venda": 1.0, "locacao": 2.0})

    def test_page_is_fetched_once_for_both_passes(self):
        a = veploy({VEPLOY_URL: veploy_page()})
        list(a.discover(a.domain, "venda"))
        list(a.discover(a.domain, "locacao"))
        self.assertEqual(a.session.fetched.count(VEPLOY_URL), 1)

    def test_empty_sitemap_is_a_loud_failure(self):
        a = veploy({}, sitemap=[])
        with self.assertRaises(AdapterError):
            a._listing_urls()


class TestVeployFields(unittest.TestCase):
    def _parse(self, **kw):
        a = veploy({VEPLOY_URL: veploy_page(**kw)})
        d = next(iter(a.discover(a.domain, "venda")))
        return a.parse(a.fetch(d))

    def test_geography_comes_from_the_title(self):
        """PostalAddress puts the NEIGHBOURHOOD in addressLocality here."""
        r = self._parse()
        self.assertEqual((r["neighborhood"], r["city"], r["state"]),
                         ("Centro", "Santos", "SP"))

    def test_useful_area_from_title_total_from_floorsize(self):
        r = self._parse()
        self.assertEqual(r["area_m2"], 51)
        self.assertEqual(r["area_total_m2"], 60)

    def test_condo_and_iptu(self):
        r = self._parse()
        self.assertEqual((r["condo_fee"], r["iptu_tax"]), (450.0, 120.0))

    def test_absent_condo_is_simply_absent(self):
        """
        The adapter omits the key rather than emitting None. Both read as
        absent downstream -- the Normalizer uses .get() -- and section 3.2
        explicitly allows adapters to be sloppy about what they do not find.
        """
        self.assertIsNone(self._parse(condo=None).get("condo_fee"))

    def test_counts_from_jsonld_and_stat_strip(self):
        r = self._parse()
        self.assertEqual((r["bedrooms"], r["bathrooms"], r["parking_spots"]), (2, 1, 1))
        self.assertEqual(r["suites"], 1)          # only in the DOM strip

    def test_amenities(self):
        self.assertEqual(self._parse()["amenities"], ["Piscina"])

    def test_internal_keys_do_not_escape(self):
        self.assertNotIn("_prices", self._parse())


# --------------------------------------------------------------------------
# microsistec_a
# --------------------------------------------------------------------------

MA_URL = "https://scimoveissantos.com.br/3350-apartamento-em-santos-bairro-aparecida.html"


def ma_page(transaction="Venda", price="R$ 405.000,00", iptu="R$ 175,00",
            condo="R$ 800,00", related=True):
    rows = f'<li class="first-item"><span>{transaction}</span><b>{price}</b></li>'
    if iptu:
        rows += f"<li><span>IPTU</span><b>{iptu}</b></li>"
    if condo:
        rows += f"<li><span>Condomínio</span><b>{condo}</b></li>"
    tail = ('<div class="most-view-item"><span>Venda</span><b>R$ 999.999,00</b>'
            '<span>Dorm.</span><br><b>9</b></div>') if related else ""
    return (
        '<html><div id="info-valores"><div class="info-valores">'
        f'<ul class="valores">{rows}</ul></div></div>'
        '<ul class="summary">'
        '<li><i class="fa fa-bed"></i><br><span>Dorm.</span><br><b>2</b></li>'
        '<li><i class="fa fa-bed"></i><br><span>Suítes</span><br><b>1</b></li>'
        '<li><i class="fa fa-car"></i><br><span>Vagas</span><br><b>1</b></li>'
        '<li><i class="fa fa-tint"></i><br><span>Banheiros</span><br><b>3</b></li>'
        '<li><span>Área (útil)</span><br><b title="100 Metros Quadrados">100 <sup>m²</sup></b></li>'
        '</ul>'
        f'{tail}</html>')


def ma(pages, sitemap=None):
    src = Source(domain="scimoveissantos.com.br", platform="microsistec_a")
    return MicrosistecAAdapter(FakeSession(pages, sitemap), src)


class TestMicrosistecATransaction(unittest.TestCase):
    def test_transaction_read_from_the_price_block(self):
        a = ma({MA_URL: ma_page("Locação", "R$ 4.500,00")})
        found = list(a.discover(a.domain, "locacao"))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].discovery_path, "page:info-valores")

    def test_sale_page_is_not_discovered_as_rental(self):
        a = ma({MA_URL: ma_page("Venda")})
        self.assertEqual(list(a.discover(a.domain, "locacao")), [])

    def test_related_listings_do_not_set_the_transaction(self):
        """
        The rail below carries "Venda / R$ 999.999,00" on a rental page. The
        `id="info-valores"` anchor is the whole defence.
        """
        a = ma({MA_URL: ma_page("Locação", "R$ 4.500,00", related=True)})
        d = next(iter(a.discover(a.domain, "locacao")))
        self.assertEqual(a.parse(a.fetch(d))["price"], 4500.0)

    def test_related_listings_do_not_set_counts(self):
        a = ma({MA_URL: ma_page("Venda", related=True)})
        d = next(iter(a.discover(a.domain, "venda")))
        self.assertEqual(a.parse(a.fetch(d))["bedrooms"], 2)   # not 9

    def test_page_without_a_price_block_is_counted_not_crashed(self):
        a = ma({MA_URL: "<html>nada aqui</html>"})
        self.assertEqual(list(a.discover(a.domain, "venda")), [])
        self.assertEqual(a.unlabelled, 1)

    def test_page_fetched_once_across_both_passes(self):
        a = ma({MA_URL: ma_page()})
        list(a.discover(a.domain, "venda"))
        list(a.discover(a.domain, "locacao"))
        self.assertEqual(a.session.fetched.count(MA_URL), 1)


class TestMicrosistecAFields(unittest.TestCase):
    def _parse(self, **kw):
        a = ma({MA_URL: ma_page(**kw)})
        t = "locacao" if kw.get("transaction") == "Locação" else "venda"
        d = next(iter(a.discover(a.domain, t)))
        return a.parse(a.fetch(d)), d

    def test_url_hints_carry_type_city_neighbourhood(self):
        _, d = self._parse()
        self.assertEqual(d.hints, {"property_type": "Apartamento",
                                   "city": "Santos",
                                   "neighborhood": "Aparecida"})

    def test_money_fields(self):
        r, _ = self._parse()
        self.assertEqual((r["price"], r["iptu_tax"], r["condo_fee"]),
                         (405000.0, 175.0, 800.0))

    def test_counts_and_area_from_the_summary_strip(self):
        r, _ = self._parse()
        self.assertEqual((r["bedrooms"], r["suites"], r["parking_spots"],
                          r["bathrooms"], r["area_m2"]), (2, 1, 1, 3, 100))

    def test_internal_keys_do_not_escape(self):
        r, _ = self._parse()
        self.assertNotIn("_transaction", r)

    def test_listing_id_is_the_numeric_code(self):
        self.assertEqual(MicrosistecAAdapter.listing_id(MA_URL), "3350")


class TestBothThroughTheNormalizer(unittest.TestCase):
    def _rows(self, adapter):
        n = Normalizer(source_domain=adapter.domain,
                       source_platform=adapter.platform,
                       extraction_date="2026-08-28")
        for t in ("venda", "locacao"):
            for d in adapter.discover(adapter.domain, t):
                p = adapter.fetch(d)
                if p is not None:
                    n.normalize(adapter.parse(p), d)
        return n

    def test_veploy_dual_yields_two_clean_rows(self):
        rows = self._rows(veploy({VEPLOY_URL: veploy_page()})).finalize()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["transaction_type"] for r in rows}, {"venda", "locacao"})
        self.assertEqual(len({r["property_id"] for r in rows}), 2)

    def test_microsistec_a_row_is_complete(self):
        rows = self._rows(ma({MA_URL: ma_page("Locação", "R$ 4.500,00")})).finalize()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["transaction_type"], "locacao")
        self.assertEqual(r["city"], "Santos")
        self.assertEqual(r["neighborhood"], "Aparecida")
        self.assertEqual(r["price"], 4500.0)

    def test_no_pii_from_either(self):
        for a in (veploy({VEPLOY_URL: veploy_page()}),
                  ma({MA_URL: ma_page()})):
            self._rows(a).finalize()   # the gate raises if anything survived


if __name__ == "__main__":
    unittest.main()
