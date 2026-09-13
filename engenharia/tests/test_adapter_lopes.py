"""
Testes do adapter `lopes`.

Fixtures SINTETICAS, com a FORMA medida no inventario de 2026-09-06 e
valores inventados. Nenhum teste toca a rede.

Alem do caminho feliz, os casos silenciosos: a fonte responde bem e o dado
vem errado. Sao eles que pegam o modo de falha caro deste projeto -- coleta
limpa, confiante e incompleta, sem exceção nenhuma.
"""
from __future__ import annotations

import json
import unittest

from coleta.anuncios.adapters import lopes
from coleta.anuncios.adapters.base import AdapterError, DiscoveredURL, Payload
from comum import privacy, schema


# --------------------------------------------------------------------- #
# dublês                                                                 #
# --------------------------------------------------------------------- #
class Resposta:
    def __init__(self, text): self.text = text


class Sessao:
    """Devolve o que o mapa disser. `None` = a busca falhou."""
    def __init__(self, mapa): self.mapa, self.pedidos = mapa, []

    def get(self, url, **kw):
        self.pedidos.append(url)
        for chave, corpo in self.mapa.items():
            if chave in url:
                return None if corpo is None else Resposta(corpo)
        return None


class Fonte:
    def __init__(self, cidade="São Paulo", uf="SP"):
        self.domain = "lopes.com.br"
        self.base_url = "https://www.lopes.com.br"
        self.city, self.uf = cidade, uf


def sitemap(*urls):
    return "<urlset>" + "".join(f"<loc>{u}</loc>" for u in urls) + "</urlset>"


def produto(sku="REO1", deals=("sale",), venda=800000.0, aluguel=0,
            condominio=1200.0, iptu=350.0, cidade="São Paulo",
            bairro="Moema", extras=None):
    p = {
        "sku": sku,
        "dealTypes": list(deals),
        "divisionType": "Apartamento",
        "description": "Apartamento reformado com varanda.",
        "prices": {"sale": venda, "rent": aluguel, "condominium": condominio,
                   "property": iptu, "squareMeters": 9876.5,
                   "firstInstallment": 4321.0, "fullMonthlyPrice": 0,
                   "priceFrom": None, "priceFromLegalText": None},
        "address": {"city": cidade, "state": "São Paulo", "stateInitials": "SP",
                    "neighborhood": bairro, "street": "Rua Inventada",
                    "formatted": "Rua Inventada - Moema"},
        "attributes": [
            {"type": "area_attr", "name": "Área", "value": "88m²"},
            {"type": "total_area_attr", "name": "Área total", "value": "95m²"},
            {"type": "bedroom_attr", "name": "Dormitórios", "value": "3"},
            {"type": "suite_attr", "name": "Suítes", "value": "1"},
            {"type": "bathroom_attr", "name": "Banheiros", "value": "2"},
            {"type": "parking_lots_attr", "name": "Vagas", "value": "0"},
            {"type": "floor_attr", "name": "Andar", "value": "12º"},
        ],
        "condominium": {"amenities": [{"name": "Piscina"}, {"name": "Academia"}]},
        "features": [{"name": "Aceita Pet"}],
        # os blocos que NUNCA podem sobreviver
        "agents": [{"name": "Fulana De Tal", "nickname": "Fulana.DeTal",
                    "creci": "329568-F",
                    "photo": {"url": "https://exemplo/foto.jpg"}}],
        "formLead": {"showWhatsapp": True, "phone": "11987654321",
                     "defaultChannel": "1"},
        "advertiser": {"name": "Imobiliária Inventada", "shortName": "II"},
        "map": {"lat": -23.6, "lng": -46.66},
        "pois": [{"name": "Gastronomia", "places": [{"name": "Bar do Zé"}]}],
    }
    if extras:
        p.update(extras)
    return json.dumps({"product": p}, ensure_ascii=False)


def monta(mapa, cidade="São Paulo"):
    s = Sessao(mapa)
    return lopes.LopesAdapter(s, Fonte(cidade=cidade)), s


URL1 = "https://www.lopes.com.br/imovel/REO1/venda-apartamento-3-quartos-sao-paulo-moema"


# --------------------------------------------------------------------- #
class TestContrato(unittest.TestCase):

    def test_allowlist_e_nevermap_nao_se_cruzam(self):
        self.assertEqual(lopes.API_FIELDS & lopes.NEVER_MAP, frozenset())

    def test_o_adapter_esta_registrado(self):
        from coleta.anuncios import adapters
        self.assertIs(adapters._REGISTRY["lopes"], lopes.LopesAdapter)


class TestCaminhoFeliz(unittest.TestCase):

    def test_descobre_e_extrai(self):
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1),
                      "sitemap-imoveis-2": sitemap(), "sitemap-imoveis-3": sitemap(),
                      "sitemap-imoveis-4": sitemap(), "products-improved": produto()})
        achados = list(a.discover("lopes.com.br", "venda"))
        self.assertEqual(len(achados), 1)
        d = achados[0]
        self.assertEqual(d.transaction_type, "venda")
        self.assertEqual(d.listing_id, "REO1-venda")

        linha = a.parse(a.fetch(d))
        self.assertEqual(linha["price"], 800000.0)
        self.assertEqual(linha["area_m2"], 88.0)
        self.assertEqual(linha["area_total_m2"], 95.0)
        self.assertEqual(linha["bedrooms"], 3)
        self.assertEqual(linha["suites"], 1)
        self.assertEqual(linha["bathrooms"], 2)
        self.assertEqual(linha["floor_level"], 12)
        self.assertEqual(linha["condo_fee"], 1200.0)
        self.assertEqual(linha["iptu_tax"], 350.0)
        self.assertEqual(linha["neighborhood"], "Moema")
        self.assertIn("Piscina", linha["amenities"])
        self.assertIn("Aceita Pet", linha["amenities"])

    def test_uma_requisicao_por_anuncio(self):
        """O detalhe responde direto; o fetch nao pode pedir de novo."""
        a, s = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto()})
        d = list(a.discover("lopes.com.br", "venda"))[0]
        antes = sum(1 for u in s.pedidos if "products-improved" in u)
        a.fetch(d)
        depois = sum(1 for u in s.pedidos if "products-improved" in u)
        self.assertEqual(antes, 1)
        self.assertEqual(depois, 1, "fetch() pediu a API de novo")


class TestAnuncioDuplo(unittest.TestCase):
    """244 imoveis da base atual estao nesta condicao. Nao e caso de borda."""

    MAPA = {"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
            "products-improved": produto(deals=("sale", "rent"),
                                         venda=1000000.0, aluguel=4500.0)}

    def test_aparece_nas_duas_passagens(self):
        for t in ("venda", "locacao"):
            a, _ = monta(dict(self.MAPA))
            with self.subTest(t=t):
                self.assertEqual(len(list(a.discover("lopes.com.br", t))), 1)

    def test_cada_linha_leva_o_seu_proprio_preco(self):
        precos = {}
        for t in ("venda", "locacao"):
            a, _ = monta(dict(self.MAPA))
            d = list(a.discover("lopes.com.br", t))[0]
            precos[t] = a.parse(a.fetch(d))["price"]
        self.assertEqual(precos, {"venda": 1000000.0, "locacao": 4500.0})

    def test_listing_id_escopado_por_transacao(self):
        ids = set()
        for t in ("venda", "locacao"):
            a, _ = monta(dict(self.MAPA))
            ids.add(list(a.discover("lopes.com.br", t))[0].listing_id)
        self.assertEqual(ids, {"REO1-venda", "REO1-locacao"})


class TestFalhaSilenciosa(unittest.TestCase):
    """A fonte responde bem e o dado vem errado."""

    def test_sitemap_vazio_levanta(self):
        a, _ = monta({"sitemap": sitemap()})
        with self.assertRaises(AdapterError):
            list(a.discover("lopes.com.br", "venda"))

    def test_zero_anuncios_da_transacao_levanta(self):
        """Só venda no catálogo e a corrida pede locação: falha, não silêncio."""
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(deals=("sale",))})
        with self.assertRaises(AdapterError):
            list(a.discover("lopes.com.br", "locacao"))

    def test_dealtypes_vence_o_slug(self):
        """
        O slug diz 'venda' e a API diz que é só locação. A API é a verdade;
        emitir pela URL produziria uma linha de venda com preço de aluguel.
        """
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(deals=("rent",), venda=0,
                                                   aluguel=3000.0)})
        with self.assertRaises(AdapterError):
            list(a.discover("lopes.com.br", "venda"))

    def test_api_devolve_html_em_vez_de_json(self):
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": "<html>manutenção</html>"})
        with self.assertRaises(AdapterError):
            list(a.discover("lopes.com.br", "venda"))

    def test_anuncio_sumido_e_resultado_ordinario(self):
        """404 na API não derruba a corrida inteira."""
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1, URL1.replace("REO1", "REO2")),
                      "sitemap-imoveis-": sitemap(),
                      "products-improved/REO1": produto(sku="REO1"),
                      "products-improved/REO2": None})
        self.assertEqual(len(list(a.discover("lopes.com.br", "venda"))), 1)

    def test_zero_de_dinheiro_vira_nulo_nunca_zero(self):
        """R$ 0,00 de condomínio significa não informado (SDD D-04)."""
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(condominio=0, iptu=0)})
        d = list(a.discover("lopes.com.br", "venda"))[0]
        linha = a.parse(a.fetch(d))
        self.assertIsNone(linha["condo_fee"])
        self.assertIsNone(linha["iptu_tax"])

    def test_zero_de_contagem_e_resposta_valida(self):
        """Zero vaga é zero vaga, não ausência."""
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto()})
        d = list(a.discover("lopes.com.br", "venda"))[0]
        self.assertEqual(a.parse(a.fetch(d))["parking_spots"], 0)

    def test_decimal_da_api_nao_passa_pelo_parser_ptbr(self):
        """`1650.00` virando 165000 já aconteceu neste projeto."""
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(venda=1650.00)})
        d = list(a.discover("lopes.com.br", "venda"))[0]
        self.assertEqual(a.parse(a.fetch(d))["price"], 1650.0)

    def test_filtro_de_cidade_nao_deixa_passar_outra(self):
        """Sitemap nacional: São Paulo pedido, Rio no slug."""
        rio = "https://www.lopes.com.br/imovel/REO9/venda-apartamento-rio-de-janeiro-tijuca"
        a, _ = monta({"sitemap-imoveis.xml": sitemap(rio), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(sku="REO9", cidade="Rio de Janeiro")})
        with self.assertRaises(AdapterError):
            list(a.discover("lopes.com.br", "venda"))


class TestPII(unittest.TestCase):
    """Nada pessoal pode sobreviver à ingestão."""

    PESSOAIS = ("Fulana De Tal", "Fulana.DeTal", "329568-F",
                "11987654321", "exemplo/foto.jpg")

    def _linha(self):
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto()})
        d = list(a.discover("lopes.com.br", "venda"))[0]
        return a, a.fetch(d), d

    def test_o_payload_ja_sai_sem_pii(self):
        """O descarte é na ingestão, não no parse nem na escrita."""
        _, payload, _ = self._linha()
        for s in self.PESSOAIS:
            self.assertNotIn(s, payload.text, f"{s!r} sobreviveu ao Payload")

    def test_a_linha_extraida_nao_tem_pii(self):
        a, payload, _ = self._linha()
        serial = json.dumps(a.parse(payload), ensure_ascii=False)
        for s in self.PESSOAIS:
            self.assertNotIn(s, serial, f"{s!r} sobreviveu ao parse")

    def test_o_gate_aprova_a_linha(self):
        a, payload, d = self._linha()
        linha = a.parse(payload)
        linha.update(property_id="a" * 20, link=d.url,
                     source_domain="lopes.com.br", source_platform="lopes",
                     extraction_date="2026-09-06")
        privacy.assert_clean([linha], "teste:lopes",
                             url_fields=schema.URL_FIELDS,
                             id_fields=schema.ID_FIELDS)

    def test_descricao_adversarial_e_redigida(self):
        adv = {"description": "Ligue (11) 98765-4321 ou fulano@exemplo.com, CRECI 12345"}
        a, _ = monta({"sitemap-imoveis.xml": sitemap(URL1), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(extras=adv)})
        d = list(a.discover("lopes.com.br", "venda"))[0]
        texto = a.parse(a.fetch(d))["description_clean"] or ""
        limpo = privacy.scrub_text(texto)
        for s in ("98765-4321", "fulano@exemplo.com", "CRECI 12345"):
            self.assertNotIn(s, limpo)


class TestConversao(unittest.TestCase):

    def test_numero_extrai_de_texto_com_unidade(self):
        self.assertEqual(lopes._numero("97m²"), 97.0)
        self.assertEqual(lopes._numero("19º"), 19.0)
        self.assertEqual(lopes._numero("1.234,5"), 1.234)   # primeiro número
        self.assertIsNone(lopes._numero("sem número"))
        self.assertIsNone(lopes._numero(None))

    def test_dinheiro_recusa_zero_e_aceita_float(self):
        self.assertIsNone(lopes._dinheiro(0))
        self.assertIsNone(lopes._dinheiro(None))
        self.assertEqual(lopes._dinheiro(5800000.0), 5800000.0)

    def test_projeta_descarta_os_blocos_proibidos(self):
        p = json.loads(produto())["product"]
        pequeno = lopes._projeta(p)
        for proibido in ("agents", "formLead", "advertiser", "map", "pois"):
            self.assertNotIn(proibido, pequeno)


if __name__ == "__main__":
    unittest.main()


class TestCidadeVazada(unittest.TestCase):
    """
    O slug nao decide a cidade.

    Medido na primeira corrida real (2026-09-06): um terreno em Jundiai entrou
    na particao de Santos porque o bairro se chama "Cidade Santos Dumont" e o
    filtro era `"santos" in slug`. Uma linha em 250, sem erro nenhum.
    """

    VAZANTE = ("https://www.lopes.com.br/imovel/REO9/"
               "venda-terreno-jundiai-cidade-santos-dumont")

    def test_bairro_com_o_nome_da_cidade_nao_passa(self):
        a, _ = monta({"sitemap-imoveis.xml": sitemap(self.VAZANTE),
                      "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(sku="REO9", cidade="Jundiaí",
                                                   bairro="Cidade Santos Dumont")},
                     cidade="Santos")
        with self.assertRaises(AdapterError):
            list(a.discover("lopes.com.br", "venda"))

    def test_a_cidade_certa_continua_passando(self):
        url = "https://www.lopes.com.br/imovel/REO8/venda-apartamento-santos-gonzaga"
        a, _ = monta({"sitemap-imoveis.xml": sitemap(url), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(sku="REO8", cidade="Santos",
                                                   bairro="Gonzaga")},
                     cidade="Santos")
        self.assertEqual(len(list(a.discover("lopes.com.br", "venda"))), 1)

    def test_acento_nao_atrapalha(self):
        """'São Paulo' da API contra 'sao paulo' do config."""
        url = "https://www.lopes.com.br/imovel/REO7/venda-apartamento-sao-paulo-moema"
        a, _ = monta({"sitemap-imoveis.xml": sitemap(url), "sitemap-imoveis-": sitemap(),
                      "products-improved": produto(sku="REO7", cidade="São Paulo")},
                     cidade="sao paulo")
        self.assertEqual(len(list(a.discover("lopes.com.br", "venda"))), 1)
