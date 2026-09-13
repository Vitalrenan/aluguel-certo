"""
Testes do adapter `vivareal`. Fixtures SINTÉTICAS -- página real tem telefone vivo.

Os casos que decidem estão em `TestFalhaSilenciosa`: a fonte responde bem e o
dado sai errado. Cada um foi visto numa página real em 2026-08-30.
"""
from __future__ import annotations

import json
import unittest

from coleta.anuncios.adapters.base import DiscoveredURL, Payload
from coleta.anuncios.adapters.vivareal import (API_FIELDS, NEVER_MAP, VivaRealAdapter, _bairro,
                               _dinheiro, _numero)

URL = ("https://www.vivareal.com.br/imovel/apartamento-2-quartos-encruzilhada-"
       "santos-com-garagem-68m2-venda-RS650000-id-2901427900/")


def no_busca(*, url=URL, area=68, andar=22, quartos=2, banheiros=2,
             rua="Rua Jorge Tibiriçá", cidade="Santos", uf="SP",
             tipo="Apartment", descricao="Apartamento na Encruzilhada."):
    n = {"@context": "https://schema.org", "@type": tipo, "url": url,
         "name": "Apartamento para comprar", "description": descricao,
         "address": {"@type": "PostalAddress", "addressCountry": "BR",
                     "addressLocality": cidade, "addressRegion": uf,
                     "streetAddress": rua},
         "numberOfBedrooms": quartos, "numberOfBathroomsTotal": banheiros}
    if area is not None:
        n["floorSize"] = {"@type": "QuantitativeValue", "unitCode": "MTK",
                          "value": area}
    if andar is not None:
        n["floorLevel"] = andar
    return n


def pagina_busca(nos, facetas=()):
    """
    A pagina real PUBLICA suas facetas como links -- e a gramatica muda entre
    cidades (`bairros/gonzaga/` em Santos, `zona-sul/itaim-bibi/` em SP).
    """
    blocos = "".join(
        f'<script type="application/ld+json">{json.dumps(n, ensure_ascii=False)}</script>'
        for n in nos)
    links = "".join(f'<a href="{c}">x</a>' for c in facetas)
    return f"<!doctype html><html><body>{links}{blocos}</body></html>"


MICRO = [("floorSize", "68 m²"), ("numberOfRooms", "2 quartos"),
         ("numberOfBathroomsTotal", "2 banheiros"),
         ("numberOfParkingSpaces", "1 vaga"), ("floorLevel", "22º andar"),
         ("numberOfSuites", "1 suíte"), ("PETS_ALLOWED", "Aceita animais"),
         ("PANORAMIC_VIEW", "Vista panorâmica")]


def caracteristicas(pares):
    """Reproduz o <li itemProp=X> ... <span class=amenities-item-text>."""
    return ('<ul data-testid="amenities-list">' + "".join(
        f'<li class="flex" itemProp="{k}"><span aria-label="{k}"></span>'
        f'<span class="amenities-item-text" data-cy="ldp">{v}</span></li>'
        for k, v in pares) + "</ul>")


def pagina_anuncio(*, preco=650000, sku="2901427900", condo="R$ 1.000/mês",
                   iptu="R$ 250", descricao="Apartamento amplo e reformado.",
                   morta=False, extra="", micro=None):
    if morta:
        return ("<!doctype html><html><body><h2>Oops.</h2>"
                "<p>Não conseguimos encontrar a página solicitada.</p>"
                '<script type="application/ld+json">'
                '{"@type":"Organization","name":"Viva Real"}</script></body></html>')
    prod = {"@context": "https://schema.org", "@type": "Product", "sku": sku,
            "description": descricao,
            "offers": {"@type": "Offer", "price": preco, "priceCurrency": "BRL"}}
    dom = ""
    if condo is not None:
        dom += f'<p class="value-item__value" data-testid="condoFee">{condo}</p>'
    if iptu is not None:
        dom += f'<p class="value-item__value" data-testid="iptu">{iptu}</p>'
    dom += caracteristicas(MICRO if micro is None else micro)
    return (f'<!doctype html><html><body>{dom}'
            f'<script type="application/ld+json">'
            f'{json.dumps(prod, ensure_ascii=False)}</script>{extra}</body></html>')


class Sessao:
    def __init__(self, paginas):
        self.paginas, self.pedidos = paginas, []

    def get(self, url, **kw):
        self.pedidos.append(url)
        corpo = self.paginas.get(url)
        if corpo is None:
            return None

        class R:
            text = corpo
            status_code = 200
            headers: dict = {}
        return R()


class Fonte:
    domain, city, state = "vivareal.com.br", "santos", "sp"


def adaptador(paginas):
    return VivaRealAdapter(session=Sessao(paginas), source=Fonte())


def analisa(html, hints=None, transacao="venda"):
    d = DiscoveredURL(url=URL, transaction_type=transacao,
                      discovery_path=f"busca/{transacao}", listing_id="2901427900",
                      hints=hints or {})
    return VivaRealAdapter(session=None, source=None).parse(
        Payload(discovered=d, text=html))


BUSCA1 = "https://www.vivareal.com.br/venda/sp/santos/"


class TestMapeamento(unittest.TestCase):

    def test_conjuntos_nao_se_cruzam(self):
        self.assertEqual(API_FIELDS & NEVER_MAP, frozenset())

    def test_busca_entrega_atributo_fisico(self):
        a = adaptador({BUSCA1: pagina_busca([no_busca()])})
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 1)
        h = d[0].hints
        self.assertEqual(h["area_m2"], 68.0)
        self.assertEqual(h["floor_level"], 22)
        self.assertEqual(h["bedrooms"], 2)
        self.assertEqual(h["bathrooms"], 2)
        self.assertEqual(h["address"], "Rua Jorge Tibiriçá")

    def test_anuncio_acrescenta_dinheiro_aos_hints(self):
        a = adaptador({BUSCA1: pagina_busca([no_busca()])})
        d = list(a.discover("vivareal.com.br", "venda"))[0]
        r = analisa(pagina_anuncio(), hints=d.hints)
        self.assertEqual(r["price"], 650000.0)
        self.assertEqual(r["condo_fee"], 1000.0)
        self.assertEqual(r["iptu_tax"], 250.0)
        self.assertEqual(r["area_m2"], 68.0)      # veio da busca, sobreviveu
        self.assertEqual(r["floor_level"], 22)


class TestMicrodados(unittest.TestCase):
    """
    A primeira corrida a seco devolveu `parking_spots`, `suites` e `amenities`
    em 0%. Zero era erro meu, não ausência: a URL já dizia "com-garagem". Os
    três vivem no microdata `itemProp` do bloco de características.
    """

    def test_vaga_suite_e_amenidade_saem_do_itemprop(self):
        r = analisa(pagina_anuncio())
        self.assertEqual(r["parking_spots"], 1)
        self.assertEqual(r["suites"], 1)
        self.assertIn("Aceita animais", r["amenities"])
        self.assertIn("Vista panorâmica", r["amenities"])

    def test_zero_suites_e_resposta_valida_nao_ausencia(self):
        """Contagem difere de dinheiro: `0 suítes` é uma resposta."""
        r = analisa(pagina_anuncio(micro=[("numberOfSuites", "0 suítes"),
                                          ("numberOfParkingSpaces", "2 vagas")]))
        self.assertEqual(r["suites"], 0)
        self.assertEqual(r["parking_spots"], 2)

    def test_plural_nao_muda_a_leitura(self):
        um = analisa(pagina_anuncio(micro=[("numberOfParkingSpaces", "1 vaga")]))
        dois = analisa(pagina_anuncio(micro=[("numberOfParkingSpaces", "2 vagas")]))
        self.assertEqual((um["parking_spots"], dois["parking_spots"]), (1, 2))

    def test_numerico_nao_vaza_para_amenidade(self):
        r = analisa(pagina_anuncio())
        for lixo in ("1 vaga", "68 m²", "2 quartos", "22º andar"):
            self.assertNotIn(lixo, r["amenities"])

    def test_anuncio_vence_a_busca_no_andar(self):
        """`floorLevel` sai 67% na busca e é mais completo no anúncio."""
        r = analisa(pagina_anuncio(micro=[("floorLevel", "9º andar")]),
                    hints={"floor_level": 22})
        self.assertEqual(r["floor_level"], 9)

    def test_par_nao_atravessa_para_o_li_vizinho(self):
        """
        Sem o `(?!itemProp=)`, o casamento pularia para o próximo <li> e
        parearia um rótulo com o texto do vizinho -- vaga viraria suíte.
        """
        r = analisa(pagina_anuncio(micro=[("numberOfParkingSpaces", "3 vagas"),
                                          ("numberOfSuites", "1 suíte")]))
        self.assertEqual(r["parking_spots"], 3)
        self.assertEqual(r["suites"], 1)


class TestFacetas(unittest.TestCase):
    """
    A busca simples esgota no teto de paginacao (~55 paginas). O volume vem das
    FACETAS -- e a gramatica delas MUDA ENTRE CIDADES, medido em 2026-09-03:

        Santos      /venda/sp/santos/bairros/gonzaga/
        Sao Paulo   /venda/sp/sao-paulo/zona-sul/itaim-bibi/

    Construir `bairros/{x}/` para Sao Paulo devolve 404 e a coleta ficaria
    presa em ~1.300 anuncios de 863 mil, sem erro nenhum. Por isso as facetas
    sao LIDAS dos links que a propria pagina publica.
    """

    CAMINHO = "/venda/sp/santos/"

    def test_faceta_publicada_pela_pagina_e_visitada(self):
        outro = no_busca(url=URL.replace("id-2901427900", "id-999"))
        a = adaptador({
            BUSCA1: pagina_busca([no_busca()],
                                 facetas=[f"{self.CAMINHO}bairros/gonzaga/"]),
            f"{BUSCA1}bairros/gonzaga/": pagina_busca([outro]),
        })
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 2, "a faceta publicada nao foi visitada")

    def test_gramatica_de_sao_paulo_funciona_sem_mudar_codigo(self):
        """Zona/bairro em vez de `bairros/` -- o adapter nao pode saber a diferenca."""
        raiz = "https://www.vivareal.com.br/venda/sp/sao-paulo/"
        outro = no_busca(url=URL.replace("id-2901427900", "id-777"))

        class FonteSP:
            domain, city, state = "vivareal.com.br", "sao-paulo", "sp"

        a = VivaRealAdapter(session=Sessao({
            raiz: pagina_busca([no_busca()],
                               facetas=["/venda/sp/sao-paulo/zona-sul/itaim-bibi/"]),
            f"{raiz}zona-sul/itaim-bibi/": pagina_busca([outro]),
        }), source=FonteSP())
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 2)
        self.assertTrue(any("zona-sul/itaim-bibi" in u for u in a.session.pedidos))

    def test_faceta_de_segundo_nivel_e_seguida(self):
        """A pagina de uma zona linka seus bairros -- Sao Paulo desce um nivel."""
        raiz = "https://www.vivareal.com.br/venda/sp/sao-paulo/"
        n2 = no_busca(url=URL.replace("id-2901427900", "id-222"))

        class FonteSP:
            domain, city, state = "vivareal.com.br", "sao-paulo", "sp"

        a = VivaRealAdapter(session=Sessao({
            raiz: pagina_busca([no_busca()], facetas=["/venda/sp/sao-paulo/zona-sul/"]),
            f"{raiz}zona-sul/": pagina_busca([], facetas=["/venda/sp/sao-paulo/zona-sul/moema/"]),
            f"{raiz}zona-sul/moema/": pagina_busca([n2]),
        }), source=FonteSP())
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 2)
        self.assertTrue(any("zona-sul/moema" in u for u in a.session.pedidos),
                        "nao seguiu a faceta de segundo nivel")

    def test_anuncio_repetido_entre_facetas_sai_uma_vez_so(self):
        n = no_busca()
        a = adaptador({
            BUSCA1: pagina_busca([n], facetas=[f"{self.CAMINHO}bairros/gonzaga/"]),
            f"{BUSCA1}bairros/gonzaga/": pagina_busca([n]),
        })
        self.assertEqual(len(list(a.discover("vivareal.com.br", "venda"))), 1)

    def test_faceta_que_404_nao_interrompe_as_seguintes(self):
        boa = no_busca(url=URL.replace("id-2901427900", "id-333"))
        a = adaptador({
            BUSCA1: pagina_busca([no_busca()], facetas=[
                f"{self.CAMINHO}bairros/inexistente/", f"{self.CAMINHO}bairros/boa/"]),
            f"{BUSCA1}bairros/boa/": pagina_busca([boa]),
        })
        self.assertEqual(len(list(a.discover("vivareal.com.br", "venda"))), 2)

    def test_com_tipo_a_raiz_sem_tipo_continua_sendo_percorrida(self):
        """
        A faceta de TIPO publica zero sub-facetas -- quem publica os links
        geográficos é a raiz. Substituir a raiz pela faceta de tipo prendeu a
        coleta de 2026-09-05 em 1.024 anúncios de uma faceta só, sem erro.
        """
        n2 = no_busca(url=URL.replace("id-2901427900", "id-444"))
        n3 = no_busca(url=URL.replace("id-2901427900", "id-555"))
        a = adaptador({
            BUSCA1: pagina_busca([no_busca()],
                                 facetas=[f"{self.CAMINHO}bairros/gonzaga/"]),
            f"{BUSCA1}apartamento_residencial/": pagina_busca([n2]),
            f"{BUSCA1}bairros/gonzaga/": pagina_busca([n3]),
        })
        a.tipo_alvo = "apartamento_residencial"
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 3, "a raiz sem tipo deixou de ser percorrida")
        self.assertTrue(any("bairros/gonzaga" in u for u in a.session.pedidos),
                        "faceta geografica publicada pela raiz nao foi visitada")

    def test_com_tipo_a_geografia_e_cruzada_com_o_tipo(self):
        """`{zona}/{bairro}/{tipo}/` -- a fonte aceita mas nao publica."""
        n2 = no_busca(url=URL.replace("id-2901427900", "id-666"))
        a = adaptador({
            BUSCA1: pagina_busca([no_busca()],
                                 facetas=[f"{self.CAMINHO}bairros/gonzaga/"]),
            f"{BUSCA1}bairros/gonzaga/apartamento_residencial/": pagina_busca([n2]),
        })
        a.tipo_alvo = "apartamento_residencial"
        list(a.discover("vivareal.com.br", "venda"))
        self.assertTrue(
            any("bairros/gonzaga/apartamento_residencial" in u
                for u in a.session.pedidos),
            "a combinacao geografia+tipo nao foi tentada")

    def test_link_para_a_propria_raiz_nao_vira_faceta(self):
        """Evita laco: a pagina linka a si mesma no menu."""
        a = adaptador({BUSCA1: pagina_busca([no_busca()], facetas=[self.CAMINHO])})
        self.assertEqual(len(list(a.discover("vivareal.com.br", "venda"))), 1)


class TestAuditoria(unittest.TestCase):
    """
    Defeitos achados pela auditoria da coleta de 3.000 em 2026-08-31, ambos
    invisíveis no dry run de 45 linhas.
    """

    def test_mesma_unidade_sob_urls_diferentes_sai_uma_vez(self):
        """
        A fonte emite o MESMO anúncio sob URLs que diferem só no preço embutido:
        `...-RS697540-id-2899535415/` e `...-RS697770-id-2899535415/`.
        Deduplicar por URL deixou passar 5 pares em 3.000, e como `property_id`
        vem de sha1(domínio + listing_id), viraram id duplicado na base.
        """
        u1 = ("https://www.vivareal.com.br/imovel/apartamento-2-quartos-"
              "encruzilhada-santos-com-garagem-127m2-venda-RS697540-id-2899535415/")
        u2 = u1.replace("RS697540", "RS697770")
        a = adaptador({BUSCA1: pagina_busca([no_busca(url=u1), no_busca(url=u2)])})
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 1, "mesma unidade saiu duas vezes")
        self.assertEqual(d[0].listing_id, "2899535415")

    def test_ids_diferentes_continuam_saindo_os_dois(self):
        """A guarda não pode colapsar imóveis que são de fato distintos."""
        u1 = URL
        u2 = URL.replace("id-2901427900", "id-2901427999")
        a = adaptador({BUSCA1: pagina_busca([no_busca(url=u1), no_busca(url=u2)])})
        self.assertEqual(len(list(a.discover("vivareal.com.br", "venda"))), 2)

    def test_segmento_do_caminho_nao_vira_tipo_de_imovel(self):
        """
        `/imovel/imovel-comercial-5-quartos-...` fazia o regex devolver
        "imovel" -- o segmento do caminho, não um tipo. Melhor None do que uma
        categoria inventada.
        """
        u = ("https://www.vivareal.com.br/imovel/imovel-comercial-5-quartos-"
             "sao-jorge-santos-com-garagem-1000m2-venda-RS3500000-id-2443295582/")
        a = adaptador({BUSCA1: pagina_busca([no_busca(url=u)])})
        h = list(a.discover("vivareal.com.br", "venda"))[0].hints
        self.assertIsNone(h["property_type"])

    def test_tipo_legitimo_continua_passando(self):
        a = adaptador({BUSCA1: pagina_busca([no_busca()])})
        h = list(a.discover("vivareal.com.br", "venda"))[0].hints
        self.assertEqual(h["property_type"], "apartamento")


class TestSemanticaDeEndereco(unittest.TestCase):
    """
    `addressLocality` = 'Santos' em 30 de 30 anúncios: é a cidade, não o bairro.
    Mapeá-lo para `neighborhood` é o erro que o `veploy` cometeu em 2.029 linhas.
    """

    def test_cidade_nao_vira_bairro(self):
        a = adaptador({BUSCA1: pagina_busca([no_busca()])})
        h = list(a.discover("vivareal.com.br", "venda"))[0].hints
        self.assertEqual(h["city"], "Santos")
        self.assertEqual(h["neighborhood"], "encruzilhada")
        self.assertNotEqual(h["neighborhood"], "Santos")

    def test_bairro_de_varias_palavras(self):
        u = ("https://www.vivareal.com.br/imovel/apartamento-1-quartos-"
             "ponta-da-praia-santos-com-garagem-58m2-venda-RS400000-id-1/")
        self.assertEqual(_bairro(u, "Santos"), "ponta da praia")

    def test_bairro_em_cidade_de_nome_composto(self):
        u = ("https://www.vivareal.com.br/imovel/apartamento-2-quartos-"
             "centro-sao-vicente-com-garagem-60m2-venda-RS300000-id-2/")
        self.assertEqual(_bairro(u, "São Vicente"), "centro")


class TestFalhaSilenciosa(unittest.TestCase):

    def test_pagina_morta_com_200_nao_entra_na_base(self):
        """
        O MESMO anúncio devolveu 404 num cliente e 200 com 293 KB e JSON-LD
        válido noutro, servindo "Oops". Sem esta checagem entraria como imóvel
        sem preço.
        """
        a = adaptador({URL: pagina_anuncio(morta=True)})
        d = DiscoveredURL(url=URL, transaction_type="venda",
                          discovery_path="busca/venda", listing_id="1")
        self.assertIsNone(a.fetch(d), "pagina 'Oops' com HTTP 200 virou anuncio")

    def test_lancamento_com_faixa_de_area_fica_de_fora(self):
        """
        A busca mistura `/imoveis-lancamentos/`, que anunciam `96 - 351 m²`.
        Uma faixa lida como valor único é dado inventado que não levanta nada.
        """
        lanc = no_busca(url="https://www.vivareal.com.br/imoveis-lancamentos/"
                            "euclydes-102-id-2845619252/", area="96 - 351")
        a = adaptador({BUSCA1: pagina_busca([lanc, no_busca()])})
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 1)
        self.assertIn("/imovel/", d[0].url)

    def test_faixa_nunca_vira_numero(self):
        self.assertIsNone(_numero("96 - 351"))
        self.assertEqual(_numero("68"), 68.0)

    def test_busca_vazia_levanta_em_vez_de_devolver_zero(self):
        a = adaptador({BUSCA1: "<html><body>nenhum resultado</body></html>"})
        with self.assertRaises(Exception) as ctx:
            list(a.discover("vivareal.com.br", "venda"))
        self.assertIn("não devolveu nenhum", str(ctx.exception))

    def test_carrossel_de_relacionados_nao_contamina(self):
        """
        A página traz outros imóveis embaixo, com preço e rótulo próprios. A
        âncora em `data-testid` é o que separa este anúncio daquilo.
        """
        carrossel = ('<div class="rec"><p>Condomínio</p><p>R$ 9.999</p>'
                     '<p>IPTU</p><p>R$ 8.888</p>'
                     '<span>R$ 999.999,00 — Venda</span></div>')
        r = analisa(pagina_anuncio(extra=carrossel))
        self.assertEqual(r["condo_fee"], 1000.0)
        self.assertEqual(r["iptu_tax"], 250.0)
        self.assertEqual(r["price"], 650000.0)

    def test_paginacao_para_quando_nao_ha_inedito(self):
        """
        Página repetida não vira laço infinito nem duplicata. A paginação da
        fonte corta em ~55, então insistir só gera 404.
        """
        p = pagina_busca([no_busca()])
        paginas = {BUSCA1: p}
        paginas.update({f"{BUSCA1}?pagina={n}": p for n in range(2, 60)})
        a = adaptador(paginas)
        d = list(a.discover("vivareal.com.br", "venda"))
        self.assertEqual(len(d), 1, "anuncio repetido virou duplicata")
        # Para na 2a pagina da busca simples; o resto sao facetas, que a
        # sessao falsa nao conhece e devolvem None de imediato.
        pedidos_da_raiz = [u for u in a.session.pedidos if "pagina=" in u]
        self.assertEqual(len(pedidos_da_raiz), 1,
                         "insistiu na paginacao depois de uma pagina sem inedito")

    def test_condominio_ausente_some_em_vez_de_virar_zero(self):
        r = analisa(pagina_anuncio(condo=None, iptu=None))
        self.assertNotIn("condo_fee", r)
        self.assertNotIn("iptu_tax", r)

    def test_zero_em_dinheiro_e_ausencia(self):
        r = analisa(pagina_anuncio(condo="R$ 0", iptu="R$ 0"))
        self.assertNotIn("condo_fee", r)
        self.assertNotIn("iptu_tax", r)

    def test_descricao_mais_longa_vence(self):
        r = analisa(pagina_anuncio(descricao="Descrição longa do anúncio inteiro."),
                    hints={"description_clean": "curta"})
        self.assertEqual(r["description_clean"], "Descrição longa do anúncio inteiro.")


class TestPII(unittest.TestCase):

    def test_nada_pessoal_sobrevive_a_ingestao(self):
        sujo = pagina_anuncio(extra=(
            '<script type="application/ld+json">'
            '{"@type":"RealEstateAgent","name":"Corretor Fulano",'
            '"telephone":"13991234567","email":"fulano@imob.com.br"}</script>'
            '<div class="broker">CRECI 123456-F — Fulano</div>'))
        r = analisa(sujo)
        serial = json.dumps(r, ensure_ascii=False)
        for pessoal in ("Corretor Fulano", "13991234567", "fulano@imob.com.br",
                        "CRECI 123456-F"):
            self.assertNotIn(pessoal, serial,
                             f"{pessoal!r} sobreviveu a projecao do registro")

    def test_campos_pessoais_estao_declarados_em_never_map(self):
        for campo in ("advertiser", "phone", "email", "creci", "RealEstateAgent"):
            self.assertIn(campo, NEVER_MAP)


class TestConversao(unittest.TestCase):

    def test_dinheiro(self):
        self.assertEqual(_dinheiro("R$ 1.000/mês"), 1000.0)
        self.assertEqual(_dinheiro("R$ 587/mês"), 587.0)
        self.assertEqual(_dinheiro("R$ 30"), 30.0)
        self.assertIsNone(_dinheiro("R$ 0"))
        self.assertIsNone(_dinheiro(None))

    def test_numero(self):
        self.assertEqual(_numero(68), 68.0)
        self.assertEqual(_numero("650000"), 650000.0)
        self.assertIsNone(_numero(0))
        self.assertIsNone(_numero(None))
        self.assertIsNone(_numero(True))     # bool nao e medida


if __name__ == "__main__":
    unittest.main()
