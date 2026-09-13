"""
JSON-LD extraction, filtered by section 2.3 before an adapter ever sees it.

Every page P0 inspected carried agency contact data in its chrome, which in
JSON-LD means an Organization node sitting beside the Product node we want.
The tests below are mostly about what does NOT come back.

Markup here is synthetic, shaped after what section 13.2 measured: Group A
emits IndividualProduct/Offer/Place/PostalAddress, Group B emits only
Product + Offer with {name, description, image, offers.price}.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comum.jsonld import extract_blocks, find, first_of_type  # noqa: E402


def script(payload):
    return f'<script type="application/ld+json">{payload}</script>'


GROUP_A = script('''{
 "@context":"https://schema.org","@type":"IndividualProduct",
 "name":"Apartamento no Embare",
 "offers":{"@type":"Offer","price":"2600.00","priceCurrency":"BRL"},
 "itemLocation":{"@type":"Place","address":{"@type":"PostalAddress",
   "addressLocality":"Santos","addressRegion":"SP","postalCode":"11075-350"}}
}''')

GROUP_B = script('''{
 "@context":"https://schema.org","@type":"Product",
 "name":"Sala comercial","description":"Otima sala",
 "image":"https://x/1.jpg",
 "offers":{"@type":"Offer","price":"180000.00","priceCurrency":"BRL"}
}''')

AGENT = script('''{
 "@context":"https://schema.org","@type":"RealEstateAgent",
 "name":"Joao Silva","telephone":"+5513981959555","email":"joao@exemplo.com.br"
}''')


class TestForbiddenTypes(unittest.TestCase):
    def test_realestateagent_block_is_never_returned(self):
        self.assertEqual(extract_blocks(AGENT), [])

    def test_person_block_is_never_returned(self):
        html = script('{"@type":"Person","name":"Maria","telephone":"13981959555"}')
        self.assertEqual(extract_blocks(html), [])

    def test_organization_chrome_is_dropped(self):
        """P0 section 5: agency phone and email are on every page."""
        html = script('{"@type":"Organization","name":"Imob X",'
                      '"telephone":"1332224444","email":"contato@x.com.br"}')
        self.assertEqual(extract_blocks(html), [])

    def test_agent_beside_a_listing_does_not_take_the_listing_down(self):
        blocks = extract_blocks(GROUP_B + AGENT)
        self.assertTrue(blocks)
        self.assertNotIn("telephone", str(blocks))


class TestContactStripping(unittest.TestCase):
    def test_contact_keys_are_stripped_from_allowed_nodes(self):
        html = script('{"@type":"Product","name":"Apto",'
                      '"seller":{"@type":"Organization","telephone":"1332224444"},'
                      '"brand":{"@type":"Organization","name":"Imob X"},'
                      '"offers":{"@type":"Offer","price":"1000"}}')
        blocks = extract_blocks(html)
        product = first_of_type(blocks, "Product")
        self.assertNotIn("seller", product)
        self.assertNotIn("brand", product)
        self.assertEqual(find(blocks, "offers", "price"), "1000")

    def test_nested_offer_survives_as_its_own_block(self):
        blocks = extract_blocks(GROUP_B)
        self.assertIsNotNone(first_of_type(blocks, "Offer"))


class TestExtraction(unittest.TestCase):
    def test_group_a_shape(self):
        blocks = extract_blocks(GROUP_A)
        self.assertEqual(find(blocks, "offers", "price"), "2600.00")
        self.assertEqual(
            find(blocks, "addressLocality", types=("PostalAddress",)), "Santos")

    def test_group_b_price_is_reachable(self):
        """Section 13.2: price is in JSON-LD on 4/4 pages, in text on 3/6."""
        self.assertEqual(find(extract_blocks(GROUP_B), "offers", "price"), "180000.00")

    def test_offers_as_a_list_reads_the_same_way(self):
        html = script('{"@type":"Product","offers":[{"@type":"Offer","price":"999"}]}')
        self.assertEqual(find(extract_blocks(html), "offers", "price"), "999")

    def test_graph_wrapper_is_flattened(self):
        html = script('{"@context":"https://schema.org","@graph":['
                      '{"@type":"Organization","telephone":"1332224444"},'
                      '{"@type":"Product","offers":{"@type":"Offer","price":"777"}}]}')
        blocks = extract_blocks(html)
        self.assertEqual(find(blocks, "offers", "price"), "777")
        self.assertNotIn("1332224444", str(blocks))

    def test_multiple_scripts_are_all_read(self):
        self.assertGreaterEqual(len(extract_blocks(GROUP_A + GROUP_B)), 4)

    def test_missing_path_returns_none_rather_than_raising(self):
        self.assertIsNone(find(extract_blocks(GROUP_B), "offers", "businessFunction"))


class TestRobustness(unittest.TestCase):
    def test_malformed_block_does_not_lose_the_page(self):
        """A broken analytics snippet is not a reason to drop the listing."""
        blocks = extract_blocks(script("{ not json ") + GROUP_B)
        self.assertEqual(find(blocks, "offers", "price"), "180000.00")

    def test_no_jsonld_returns_empty(self):
        self.assertEqual(extract_blocks("<html><body>nada</body></html>"), [])

    def test_empty_input(self):
        self.assertEqual(extract_blocks(""), [])
        self.assertEqual(extract_blocks(None), [])

    def test_attribute_order_and_quoting_variants_are_matched(self):
        html = ("<script data-x='1' TYPE=\"application/ld+json\" async>"
                '{"@type":"Product","offers":{"@type":"Offer","price":"42"}}</script>')
        self.assertEqual(find(extract_blocks(html), "offers", "price"), "42")


if __name__ == "__main__":
    unittest.main()
