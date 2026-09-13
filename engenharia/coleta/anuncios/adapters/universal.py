"""
Universal Software adapter -- the /retornar-imoveis-disponiveis JSON API.

Covers consultareimoveis.com.br and spolaorimoveis.com.br.

P0 §4.2 wrote this platform off as the expensive one: its sitemap returned 410
on 6 of 6 sampled listings, so enumeration was expected to need a category-page
crawl. The P4 probe opened one search page, read the network panel, and found a
JSON API instead -- the same lesson §14 taught on `microsistec_b`, learned a
second time on the platform the plan had ranked hardest.

    POST /retornar-imoveis-disponiveis   (form-encoded)
      finalidade=venda|aluguel & numeropagina=N & numeroregistros=100
      -> {"lista": [...], "quantidade": N, "favoritos": []}

Two differences from `microsistec_b` worth knowing before reading the code.

**1. The transaction is an INPUT, not a field.** `finalidade` selects the
result set, so each transaction is its own set of requests and the catalogue
is cached per transaction rather than once per domain. This is exactly the
discovery-derived provenance the contract was built for (§13.3): `discover()`
knows the transaction because it asked for it.

**2. `aluguel`, not `locacao`.** The platform's rental keyword is `aluguel`;
sending `locacao` returns `quantidade: 0` with HTTP 200 -- a silent empty
result, not an error. Measured on both domains. Getting this wrong would have
produced a clean, confident, entirely sale-only harvest.

The endpoint needs a session cookie, so `discover()` primes the session with
one ordinary GET of a page robots.txt permits before posting.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from urllib.parse import urlsplit

from . import register
from .base import AdapterError, DiscoveredURL, ListingSource, Payload

log = logging.getLogger(__name__)

import json

PER_PAGE = 100
MAX_PAGES = 60

# Endpoint de detalhe. Devolve 214 campos contra os 37 da busca.
#
# MEDIDO em 2026-08-28: ele NÃO aceita o código no corpo -- `codigo`,
# `codigoimovel`, `cod` e `id` devolvem HTTP 500. Depende de estado de sessão
# estabelecido pela visita à página do anúncio. Portanto são DUAS requisições
# por imóvel, e não há como reduzir para uma.
DETAIL_PATH = "/retornar-detalhe-imovel"

# As 52 flags binárias de amenidade. Diferente das outras plataformas, aqui
# existe ausência EXPLÍCITA: '0' significa "não tem", não "não informado".
# Persistimos só as ligadas em `amenities`, para manter compatibilidade com as
# outras três fontes; a informação de ausência explícita se perde e isso está
# registrado como decisão a revisitar na etapa de normalização.
AMENITY_FLAGS = {
    "piscina": "Piscina", "academia": "Academia", "churrasqueira": "Churrasqueira",
    "salaofestas": "Salão de Festas", "salaojogos": "Salão de Jogos", "sauna": "Sauna",
    "playground": "Playground", "quadraesportiva": "Quadra Esportiva",
    "quadratenis": "Quadra de Tênis", "beachtenis": "Beach Tennis",
    "espacogourmet": "Espaço Gourmet", "hidromassagem": "Hidromassagem",
    "homecinema": "Home Cinema", "salamassagem": "Sala de Massagem",
    "quadrasquash": "Quadra de Squash",
    "portaria24horas": "Portaria 24 horas", "seguranca24horas": "Segurança 24 horas",
    "interfone": "Interfone", "portaoeletronico": "Portão Eletrônico",
    "cercaeletrica": "Cerca Elétrica", "alarme": "Alarme",
    "circuitotv": "Circuito Interno de TV", "gascanalizado": "Gás Canalizado",
    "aguaindividual": "Água Individual", "wifi": "Wi-Fi", "tvacabo": "TV a Cabo",
    "mobiliado": "Mobiliado", "arcondicionado": "Ar-Condicionado",
    "armariocozinha": "Armários na Cozinha", "armarioquarto": "Armário no Quarto",
    "armariobanheiro": "Armário no Banheiro", "aquecedorgas": "Aquecedor a Gás",
    "aquecedorsolar": "Aquecedor Solar", "aquecedoreletrico": "Aquecedor Elétrico",
    "lareira": "Lareira", "permiteanimais": "Aceita Animais",
    "closet": "Closet", "lavabo": "Lavabo", "despensa": "Despensa",
    "escritorio": "Escritório", "areaservico": "Área de Serviço",
    "jardim": "Jardim", "quintal": "Quintal", "gramado": "Gramado",
    "vistamar": "Vista para o Mar", "vistamontanha": "Vista para a Montanha",
    "vistalago": "Vista para o Lago", "solmanha": "Sol da Manhã",
    "varandagourmet": "Varanda Gourmet", "box": "Box",
    "dce": "Dependência de Empregada", "rouparia": "Rouparia",
}

# Campos do detalhe que viram coluna do schema.
DETAIL_FIELDS = frozenset({
    "codigo", "descricao", "valorcondominio", "valoriptu",
    "numeroandar", "areaprincipal", "areaprivativa",
    "cep",
}) | frozenset(AMENITY_FLAGS)

# Nunca mapeados do detalhe: contato, dados de cartório, endereço completo.
DETAIL_NEVER_MAP = frozenset({
    "captadores", "fotos", "fotos360", "fotosplanta",
    "cartorio", "matriculacartorio", "livrocartorio", "folhacartorio",
    # Endereço: decisão tomada em 2026-08-29, depois de medir (n=59, ambos os
    # domínios): `cep`, `endereco` e `numero` vêm preenchidos em 100% dos
    # anúncios. O que faltava era a decisão, não o dado.
    #
    # `cep` PASSA a ser mapeado. Endereço de imóvel anunciado é atributo do
    # imóvel e é público -- §2.1. O CEP identifica a quadra, que é o que
    # explica preço.
    #
    # `endereco` e `numero` continuam fora: o número da porta identifica a
    # unidade sem acrescentar nada que o modelo use.
    #
    # `latitude`/`longitude` continuam fora por serem inúteis, não por
    # política: a API arredonda para 2 casas (~1,1 km), grade mais grossa que
    # o próprio nome do bairro -- §14.5 do plano de refatoração.
    "endereco", "numero", "complemento", "pontoreferencia",
    "latitude", "longitude",
})

# The platform's own word for each transaction. `locacao` is accepted by the
# endpoint and returns zero rows -- see the module docstring.
FINALIDADE = {"venda": "venda", "locacao": "aluguel"}

# Fields we map. Everything else is dropped at ingestion -- notably
# `captadores`, a list of {codigo, nome, email, telefone, foto, creci}: the
# same bundled-PII situation as `receiver1` on microsistec_b (§14.4), and the
# same treatment.
API_FIELDS = frozenset({
    "codigo", "url_amigavel",
    "tipo", "bairro", "cidade", "estado",
    "valor", "valortratado",
    "areainterna", "areaprincipaltratado",
    "numeroquartos", "numerobanhos", "numerosuites", "numerovagas",
    "codigofinalidade",
})

NEVER_MAP = frozenset({
    "captadores",          # broker name, email, phone, CRECI, photo
    "fotos", "fotos360",   # image URLs; not persisted
    "titulo",              # SEO string built from the columns we already keep
    # Deferred by §14.5 as a policy decision, not an extraction detail.
    "latitude", "longitude",
})

# Page used to prime the session cookie. The home page, deliberately: the
# search path `/venda/imoveis` works on consultare but 500s on spolaor, and a
# priming request must be the one page every build of the platform serves.
PRIME_PATH = "/"

# Empty form fields the endpoint expects. Sending a partial body works, but
# sending what the site's own JS sends is less likely to surprise it.
_BASE_QUERY = {
    "destaque": 0, "codigocidade": 0, "codigoregiao": 0, "codigosimovei": 0,
    "areaate": 0, "areade": 0, "areaexternaate": 0, "areaexternade": 0,
    "numerobanhos": 0, "numeroquartos": 0, "numerosuite": 0, "numerovagas": 0,
    "numeroelevador": 0, "numerovaranda": 0, "valorate": 0, "valorde": 0,
    "codigoOpcaoimovel": 0, "codigocaptador": 0, "codigocondominio": 0,
    "codigoproprietario": 0, "retornomapaapp": "false",
    "bairros[0][nomeUrl]": "todos-os-bairros", "bairros[0][nome]": "Todos",
    "condominio[codigo]": 0, "condominio[nomeUrl]": "todos-os-condominios",
    "opcaoimovel[codigo]": 0, "opcaoimovel[nomeUrl]": "todas-as-opcoes",
}


def _money(value):
    """
    'R$ 790.000,00' -> 790000.0, or None.

    Unlike microsistec_b, this platform emits pt-BR currency strings, so
    `schema.clean_currency` is the right parser and the Normalizer can do it.
    We still parse here because `valortratado` -- an integer of the same
    amount -- is cheaper and unambiguous when present.
    """
    if value in (None, "", 0, "0"):
        return None
    try:
        return float(value) or None
    except (TypeError, ValueError):
        pass
    s = str(value).replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s) or None
    except ValueError:
        return None


def _count(value):
    """Counts arrive as strings. Zero is a real answer; missing is None."""
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _area(record):
    """
    Useful area in m2.

    `areaprincipaltratado` is the area x100 as an integer (7820 -> 78.2);
    `areainterna` is the pt-BR string ('78,20'). Prefer the integer, fall
    back to the string, and treat 0 as absent -- a property with no area is
    a property whose area was not filled in.
    """
    tratado = _count(record.get("areaprincipaltratado"))
    if tratado:
        return int(tratado / 100) or None
    raw = str(record.get("areainterna") or "").replace(".", "").replace(",", ".")
    try:
        return int(float(raw)) or None
    except ValueError:
        return None


@register
class UniversalAdapter(ListingSource):
    platform = "universal"
    supports_feed = False
    jsonld_required = False

    def __init__(self, session, source):
        super().__init__(session, source)
        self._by_transaction: dict[str, list[dict]] = {}
        self._primed = False
        self.detail_failures = 0
        # Both domains redirect the bare host to `www.`, and a 301 turns a
        # POST into a GET -- the endpoint then answers with an HTML page and
        # the run looks like "no listings". The canonical host is learned from
        # the priming request rather than hard-coded, so `source_domain` stays
        # the clean provenance key it is everywhere else.
        self._base = source.base_url

    @property
    def search_url(self) -> str:
        return f"{self._base}/retornar-imoveis-disponiveis"

    # -- ingestion ---------------------------------------------------------

    def _project(self, record: dict) -> dict:
        """Keep the mapped fields, drop the rest -- at the moment of decoding."""
        return {k: v for k, v in record.items() if k in API_FIELDS}

    def _load(self, transaction: str) -> list[dict]:
        finalidade = FINALIDADE.get(transaction)
        if finalidade is None:
            raise AdapterError(f"unsupported transaction {transaction!r}")

        if transaction in self._by_transaction:
            return self._by_transaction[transaction]

        if not self._primed:
            # The endpoint answers an unprimed POST with an error page.
            resp = self.session.prime(PRIME_PATH)
            landed = getattr(resp, "url", None)
            if landed:
                parts = urlsplit(landed)
                self._base = f"{parts.scheme}://{parts.netloc}"
            self._primed = True

        records: list[dict] = []
        page, total = 1, None

        while page <= MAX_PAGES:
            body = dict(_BASE_QUERY)
            body.update({"finalidade": finalidade,
                         "numeropagina": page,
                         "numeroregistros": PER_PAGE})
            resp = self.session.post(self.search_url, data=body)
            if resp is None:
                break
            try:
                payload = resp.json()
            except ValueError as exc:
                # viveremsantosimoveis runs an older build with no such
                # endpoint and answers with an HTML 404 page. That is a
                # per-source fact, not a crash.
                raise AdapterError(
                    f"{self.domain}: {self.search_url} did not return JSON "
                    f"(this build may predate the endpoint)") from exc

            if not isinstance(payload, dict) or "lista" not in payload:
                raise AdapterError(f"{self.domain}: unexpected payload shape")

            chunk = payload.get("lista") or []
            if not chunk:
                break
            records.extend(self._project(r) for r in chunk)

            total = payload.get("quantidade")
            if total is not None and len(records) >= int(total):
                break
            page += 1

        log.info("%s: %d %s listing(s) in %d request(s)",
                 self.domain, len(records), transaction, page)
        self._by_transaction[transaction] = records
        return records

    # -- the contract ------------------------------------------------------

    def discover(self, domain: str, transaction: str) -> Iterator[DiscoveredURL]:
        for record in self._load(transaction):
            code = record.get("codigo")
            slug = record.get("url_amigavel")
            # Explicit rather than falsy: a listing code of 0 is not realistic
            # on this platform, but "absent" and "zero" are different facts and
            # the boundary should not conflate them.
            if code in (None, "") or not slug:
                continue
            yield DiscoveredURL(
                url=f"{self.source.base_url}/imovel/{slug}/{code}",
                transaction_type=transaction,
                discovery_path=f"api:finalidade={FINALIDADE[transaction]}",
                listing_id=f"{code}-{transaction}",
                hints={"property_type": record.get("tipo")},
            )

    def fetch(self, discovered: DiscoveredURL) -> Payload | None:
        """
        Registro da busca, enriquecido com o detalhe.

        Duas requisições: a página do anúncio (que estabelece a sessão) e o
        endpoint de detalhe. Medido que não há caminho de uma requisição só --
        ver a nota em DETAIL_PATH.

        Se o detalhe falhar, a linha ainda sai com os 37 campos da busca. Perder
        as amenidades de um imóvel não justifica perder o imóvel.
        """
        code = str(discovered.listing_id).rsplit("-", 1)[0]
        transaction = discovered.transaction_type
        base = None
        for record in self._by_transaction.get(transaction, ()):
            if str(record.get("codigo")) == code:
                base = dict(record)
                break
        if base is None:
            return None

        detalhe = self._detalhe(discovered.url)
        if detalhe:
            base.update(detalhe)
        else:
            self.detail_failures += 1

        return Payload(discovered=discovered, text=json.dumps(base),
                       content_type="application/json")

    def _detalhe(self, listing_url: str) -> dict | None:
        """Visita a página do anúncio, depois chama o endpoint de detalhe."""
        if self.session.get(listing_url) is None:
            return None
        resp = self.session.post(f"{self._base}{DETAIL_PATH}")
        if resp is None:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        # Projeção na ingestão: contato, cartório e endereço completo são
        # descartados aqui, antes de qualquer cache (§8.2 do SDD).
        return {k: v for k, v in payload.items()
                if k in DETAIL_FIELDS and k not in DETAIL_NEVER_MAP}

    def parse(self, payload: Payload) -> dict:
        record = json.loads(payload.text)
        return {
            "link": payload.discovered.url,
            "property_type": record.get("tipo"),
            "city": record.get("cidade"),
            "state": record.get("estado"),
            "neighborhood": record.get("bairro"),
            # Só o `cep`; `cep_prefix` sai dele no coerce do schema.
            "cep": record.get("cep"),

            "price": _money(record.get("valortratado")) or _money(record.get("valor")),

            "area_m2": _area(record),
            "bedrooms": _count(record.get("numeroquartos")),
            "bathrooms": _count(record.get("numerobanhos")),
            "suites": _count(record.get("numerosuites")),
            "parking_spots": _count(record.get("numerovagas")),

            # --- vindos do endpoint de detalhe (podem faltar se ele falhar) ---
            "condo_fee": _money(record.get("valorcondominio")),
            "iptu_tax": _money(record.get("valoriptu")),
            "floor_level": _count(record.get("numeroandar")),
            # `area_total_m2` NÃO é mapeado nesta plataforma, de propósito.
            # Medido em 2026-08-29: `areaprincipal` (78,20) é idêntico a
            # `areainterna`, que já vira `area_m2`. Mapeá-lo duplicaria a área
            # útil sob o nome de área total. Os outros campos de área do
            # payload são `areaexterna`, `arealote`, `areapatio` e
            # `areaarmazenagem` -- nenhum tem a semântica de "área total" que
            # as outras plataformas usam. A fonte simplesmente não tem o campo.
            "description_clean": (record.get("descricao") or "").strip() or None,
            "amenities": [rotulo for campo, rotulo in AMENITY_FLAGS.items()
                          if str(record.get(campo)) == "1"],
        }


__all__ = ["UniversalAdapter", "API_FIELDS", "NEVER_MAP", "FINALIDADE"]
