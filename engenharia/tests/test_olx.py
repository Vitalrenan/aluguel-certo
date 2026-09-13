"""
Testes do adapter `olx`. Fixtures SINTÉTICAS -- página real tem telefone vivo.

Os casos que importam são os silenciosos: a fonte responde 200, o dado sai
errado, nada levanta. Cada armadilha aqui foi medida numa página real em
2026-08-30 e está registrada em MEDICOES §17.
"""
from __future__ import annotations

import json
import unittest

from coleta.anuncios.adapters.base import DiscoveredURL, Payload
from coleta.anuncios.adapters.olx import (API_FIELDS, NEVER_MAP, OlxAdapter, _area, _dinheiro,
                          _inteiro, _propriedades, _transacao_e_tipo)


def prop(nome, label, valor):
    v = "null" if valor is None else json.dumps(valor, ensure_ascii=False)
    return ('{"name":' + json.dumps(nome) + ',"label":' + json.dumps(label)
            + ',"value":' + v + ',"values":null,"url":null}')


def pagina(*, transacao="Venda - apartamento padrão", size="70m²", rooms="2",
           bathrooms="2", garagem="1", condominio="R$ 850", iptu="R$ 375",
           features="Academia, Ar condicionado", complex_features="Elevador, Piscina",
           listid="1530848093", price="R$ 495.000", zipcode="11035220",
           bairro="Boqueirão", cidade="Santos", descricao="Apartamento reformado.",
           iptu_no_datalayer=None, extra_ld="", extra_html=""):
    """HTML sintético com a mesma FORMA da página real do OLX."""
    props = ",".join(filter(None, [
        prop("category", "Categoria", "Apartamentos"),
        prop("real_estate_type", "Tipo", transacao),
        prop("condominio", "Condomínio", condominio),
        prop("iptu", "IPTU", iptu),
        prop("size", "Área útil", size),
        prop("rooms", "Quartos", rooms),
        prop("bathrooms", "Banheiros", bathrooms),
        prop("garage_spaces", "Vagas na garagem", garagem),
        prop("re_features", "Detalhes do imóvel", features),
        prop("re_complex_features", "Detalhes do condomínio", complex_features),
    ]))
    ld = json.dumps({"@context": "https://schema.org", "@type": "BuyAction",
                     "Object": {"@type": "Product", "description": descricao}},
                    ensure_ascii=False)
    dl = ""
    if iptu_no_datalayer is not None:
        v = "null" if iptu_no_datalayer is None else json.dumps(iptu_no_datalayer)
        dl = ('<script>window.dataLayer=[{"condominio":"R$ 850","iptu":' + v
              + ',"size":70}];</script>')
    return f"""<!doctype html><html><body>
    {dl}
    <script>window.__ad={{"listId":{listid},"subject":"Apartamento",
      "price":{json.dumps(price)},"zipcode":{json.dumps(zipcode)},
      "neighbourhood":{json.dumps(bairro)},"municipality":{json.dumps(cidade)},
      "properties":[{props}]}};</script>
    <script type="application/ld+json">{ld}</script>{extra_ld}
    {extra_html}</body></html>"""


def carrega(html, transacao="venda", url="https://sp.olx.com.br/imoveis/x-1530848093"):
    d = DiscoveredURL(url=url, transaction_type=transacao,
                      discovery_path=f"busca/{transacao}", listing_id="1530848093",
                      hints={"city": "santos", "state": "sp"})
    return OlxAdapter(session=None, source=None).parse(Payload(discovered=d, text=html))


class TestMapeamento(unittest.TestCase):

    def test_conjuntos_nao_se_cruzam(self):
        self.assertEqual(API_FIELDS & NEVER_MAP, frozenset())

    def test_caminho_feliz(self):
        r = carrega(pagina())
        self.assertEqual(r["transaction_type"], "venda")
        self.assertEqual(r["price"], 495000.0)
        self.assertEqual(r["area_m2"], 70.0)
        self.assertEqual(r["bedrooms"], 2)
        self.assertEqual(r["parking_spots"], 1)
        self.assertEqual(r["cep"], "11035220")
        self.assertEqual(r["neighborhood"], "Boqueirão")
        self.assertEqual(r["condo_fee"], 850.0)
        self.assertEqual(r["iptu_tax"], 375.0)
        self.assertEqual(r["amenities"],
                         ["Academia", "Ar condicionado", "Elevador", "Piscina"])


class TestArmadilhasMedidas(unittest.TestCase):
    """Cada uma vista numa página real em 2026-08-30."""

    def test_iptu_vem_do_properties_nao_do_datalayer(self):
        """
        ARMADILHA 1. A página real traz `"iptu":null` no dataLayer e
        `"iptu":"R$ 375"` no array `properties`, para o MESMO anúncio.
        Ler a cópia errada daria IPTU 100% nulo.
        """
        r = carrega(pagina(iptu="R$ 375", iptu_no_datalayer=None))
        self.assertEqual(r["iptu_tax"], 375.0,
                         "leu a copia nula do dataLayer em vez do properties")

    def test_zero_em_dinheiro_e_ausencia_nao_valor(self):
        """ARMADILHA 2. `R$ 0` é "não informado", não uma taxa de zero real."""
        r = carrega(pagina(condominio="R$ 0", iptu="R$ 0"))
        self.assertNotIn("condo_fee", r)
        self.assertNotIn("iptu_tax", r)

    def test_cinco_ou_mais_vira_cinco(self):
        """ARMADILHA 3. Balde categórico onde se espera número. 5 é o piso."""
        self.assertEqual(_inteiro("5 ou mais"), 5)
        r = carrega(pagina(bathrooms="5 ou mais"))
        self.assertEqual(r["bathrooms"], 5)

    def test_aluguel_da_plataforma_vira_locacao_do_schema(self):
        """ARMADILHA 4. O OLX diz `Aluguel`; o schema diz `locacao`."""
        r = carrega(pagina(transacao="Aluguel - apartamento padrão"),
                    transacao="locacao")
        self.assertEqual(r["transaction_type"], "locacao")

    def test_transacao_desconhecida_nao_estoura(self):
        r = carrega(pagina(transacao="Permuta - apartamento"), transacao="venda")
        self.assertEqual(r["transaction_type"], "venda")  # cai no caminho


class TestFalhaSilenciosa(unittest.TestCase):

    def test_busca_vazia_levanta_em_vez_de_devolver_zero(self):
        """
        Termo de busca errado devolve 200 com zero resultados. Sem esta guarda,
        a coleta terminaria limpa, confiante e vazia.
        """
        class SessaoVazia:
            def get(self, url):
                class R:
                    text = "<html><body>nenhum resultado</body></html>"
                return R()

        class Fonte:
            domain, city, state = "olx.com.br", "santos", "sp"

        a = OlxAdapter(session=SessaoVazia(), source=Fonte())
        with self.assertRaises(Exception) as ctx:
            list(a.discover("olx.com.br", "venda"))
        self.assertIn("não devolveu nenhum", str(ctx.exception))

    def test_carrossel_de_relacionados_nao_contamina(self):
        """
        A página tem outros imóveis abaixo. O array `properties` do anúncio vem
        primeiro; a primeira ocorrência de cada nome vence.
        """
        carrossel = "<div class='rec'>" + prop("condominio", "Condomínio", "R$ 9.999") + "</div>"
        r = carrega(pagina(condominio="R$ 850", extra_html=carrossel))
        self.assertEqual(r["condo_fee"], 850.0)

    def test_descricao_pega_a_mais_longa_entre_tipos_de_ld(self):
        """
        O `@type` do JSON-LD muda entre venda e locação, e o nó curto é a meta
        description. Filtrar por tipo mediu `description` a 30% quando é 100%.
        """
        curto = ('<script type="application/ld+json">'
                 + json.dumps({"@type": "WebSite", "description": "OLX Brasil"})
                 + "</script>")
        r = carrega(pagina(descricao="Apartamento amplo e reformado no Boqueirao.",
                           extra_ld=curto))
        self.assertEqual(r["description_clean"],
                         "Apartamento amplo e reformado no Boqueirao.")

    def test_campo_ausente_some_em_vez_de_virar_zero(self):
        r = carrega(pagina(iptu=None, size=None))
        self.assertNotIn("iptu_tax", r)
        self.assertNotIn("area_m2", r)


class TestPII(unittest.TestCase):

    def test_nada_pessoal_sobrevive_a_ingestao(self):
        """
        A página real traz telefone, CRECI e nome do corretor. O registro
        projetado não pode conter nenhum deles -- nem em campo, nem em valor.
        """
        sujo = pagina(extra_html=(
            '<script>window.__seller={"sellerName":"Corretor Fulano",'
            '"phone":"13991234567","creci":"CRECI 123456-F",'
            '"email":"fulano@imobiliaria.com.br"};</script>'
            '<a href="https://apigw.olx.com.br/v1/showphone">Ver telefone</a>'))
        r = carrega(sujo)
        serial = json.dumps(r, ensure_ascii=False)
        for pessoal in ("Corretor Fulano", "13991234567", "CRECI 123456-F",
                        "fulano@imobiliaria.com.br", "showphone"):
            self.assertNotIn(pessoal, serial,
                             f"{pessoal!r} sobreviveu a projecao do registro")

    def test_campos_pessoais_estao_declarados_em_never_map(self):
        for campo in ("seller", "phone", "email", "creci", "showphone"):
            self.assertIn(campo, NEVER_MAP)


class TestConversao(unittest.TestCase):

    def test_dinheiro(self):
        self.assertEqual(_dinheiro("R$ 1.234"), 1234.0)
        self.assertEqual(_dinheiro("R$ 1.249.000"), 1249000.0)
        self.assertIsNone(_dinheiro("R$ 0"))
        self.assertIsNone(_dinheiro(None))
        self.assertIsNone(_dinheiro("sob consulta"))

    def test_area(self):
        self.assertEqual(_area("70m²"), 70.0)
        self.assertEqual(_area("190m²"), 190.0)
        self.assertIsNone(_area("0m²"))

    def test_inteiro_zero_e_resposta_valida(self):
        """Contagem difere de dinheiro: zero vaga é uma resposta."""
        self.assertEqual(_inteiro("0"), 0)

    def test_transacao_e_tipo(self):
        self.assertEqual(_transacao_e_tipo("Venda - apartamento padrão"),
                         ("venda", "apartamento padrão"))
        self.assertEqual(_transacao_e_tipo("Aluguel - casa"), ("locacao", "casa"))
        self.assertEqual(_transacao_e_tipo(None), (None, None))

    def test_propriedades_primeira_ocorrencia_vence(self):
        html = prop("iptu", "IPTU", "R$ 100") + prop("iptu", "IPTU", "R$ 999")
        self.assertEqual(_propriedades(html)["iptu"], "R$ 100")


if __name__ == "__main__":
    unittest.main()
