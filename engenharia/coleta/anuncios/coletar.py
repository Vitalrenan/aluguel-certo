"""
Collector -- iterate sources x targets, source-agnostically (§3.1, §7).

Replaces `step_1_scraper.py`, which knew about Zap specifically and read only
`sites[0]` of its config (SDD D-17). This module knows nothing about any
platform: it resolves adapters from the registry, hands each one a polite
Fetcher, and pushes everything they return through the Normalizer.

Run:
    python collect.py --config sources.yaml
    python collect.py --config sources.yaml --platform microsistec_a --limit 20
    python collect.py --config sources.yaml --dry-run

Exit codes matter here. A run that collects nothing exits non-zero, because
the failure Cloudflare exposed was a pipeline that reported success while
writing zero rows. A PII gate failure exits 2 and is never caught.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from coleta.anuncios import adapters
from comum import lake, schema
from comum.config import Config, Source, Target, load_config
from comum.fetch import Fetcher, SourceBlocked
from comum.normalizer import Normalizer, derive_property_id
from comum.privacy import PIIViolation

log = logging.getLogger("collect")

EXIT_OK = 0
EXIT_NOTHING_COLLECTED = 1
EXIT_PII = 2


@dataclass
class SourceResult:
    source: Source
    target: Target
    records: list = field(default_factory=list)
    stats: object = None
    fetch_stats: object = None
    skipped: str | None = None
    error: str | None = None
    path: str | None = None

    @property
    def ok(self) -> bool:
        return self.skipped is None and self.error is None and bool(self.records)


class Collector:
    def __init__(self, config: Config, writer: lake.LakeWriter | None = None,
                 limit: int | None = None, transacao: str | None = None,
                 mes: str | None = None, cidade: str | None = None,
                 tipo: str | None = None):
        self.config = config
        self.limit = limit
        self.transacao = transacao
        # Mes de referencia (AAAA-MM). Define o que conta como "ja coletado":
        # tudo que esta no lake DESTE mes e pulado. Coleta antiga nao trava a
        # nova -- um anuncio de agosto pode e deve ser recoletado em setembro,
        # porque preco e disponibilidade mudam.
        self.mes = (mes or date.today().strftime("%Y-%m")).strip()
        self.cidade = (cidade or "").strip().lower() or None
        self.tipo = (tipo or "").strip().lower() or None
        self.writer = writer or lake.LakeWriter(
            raw_dir=config.raw_dir, dry_run=config.dry_run)
        self.results: list[SourceResult] = []

    # -- one source --------------------------------------------------------

    def collect_source(self, source: Source, target: Target) -> SourceResult:
        result = SourceResult(source=source, target=target)

        if not adapters.is_registered(source.platform):
            # Not an error: the config lists every usable P0 site, and most of
            # them are waiting on an adapter. Reported, not silently skipped.
            result.skipped = f"no adapter registered for platform {source.platform!r}"
            return result

        fetcher = Fetcher(source)
        adapter = adapters.build(source, fetcher, target)
        if self.tipo is not None and hasattr(adapter, "tipo_alvo"):
            adapter.tipo_alvo = self.tipo
        elif self.tipo is not None:
            log.warning("%s nao suporta --tipo -- ignorado", source.platform)
        normalizer = Normalizer(
            source_domain=source.domain,
            source_platform=source.platform,
            target=target if len(self.config.targets) == 1 else None,
            extraction_date=self.writer.extraction_date,
        )

        try:
            self._harvest(adapter, fetcher, normalizer, source, target)
        except SourceBlocked as exc:
            # The host told us to stop. Drop the source for this run and keep
            # whatever it gave us before that (§5).
            result.error = f"source blocked: {exc}"
            log.warning("%s: %s", source.domain, exc)
        except PIIViolation:
            raise
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("%s failed", source.domain)
        finally:
            fetcher.close()

        result.stats = normalizer.stats
        result.fetch_stats = fetcher.stats

        # allow_empty: a source yielding nothing is reported per-source and
        # judged at the run level. One dead sitemap must not abort the other
        # fifteen domains. A PII hit, by contrast, propagates out of the run.
        result.records = normalizer.finalize(allow_empty=True)

        if result.records:
            result.path = self.writer.write(
                result.records, target.state, target.city,
                source.platform, source.domain)
        elif result.error is None and result.skipped is None:
            result.error = "0 rows collected"

        return result

    def _harvest(self, adapter, fetcher: Fetcher, normalizer: Normalizer,
                 source: Source, target: Target) -> None:
        # Sem `--limit`, NAO ha teto. O teto silencioso de 5.000 por fonte que
        # vivia no `sources.yaml` foi removido em 2026-09-07: ninguem o pediu,
        # ele nao aparecia em lugar nenhum da saida, e um limite que o operador
        # nao escolheu e um limite que ele nao sabe que existe.
        #
        # Quando existe, o teto vale POR TRANSACAO. Antes era um contador so
        # para todas: com `transactions: [venda, locacao]` e `--limit 250`, a
        # venda consumia as 250 e a locacao nunca rodava -- sem aviso. Foi o
        # que aconteceu na coleta de 2026-09-06 e custou uma corrida inteira.
        budget = self.limit
        # Progresso a cada PASSO anuncios. Sem isto uma coleta longa so escreve
        # log em ERRO, e silencio fica indistinguivel de travamento: em
        # 2026-08-31 li um processo pendurado por hibernacao como bloqueio do
        # site, e a projecao de tempo que fiz em cima do relogio estava
        # contaminada pelas horas dormidas (MEDICOES §9).
        PASSO = 50
        t0 = time.time()

        # SEMPRE consulta o que ja veio no mes. Antes era opcional
        # (`--novos-apenas`) e o default repetia a corrida anterior inteira.
        conhecidos = self._ja_coletados(source, target)
        if conhecidos:
            log.info("%s: %d anuncio(s) ja no lake serao pulados",
                     source.domain, len(conhecidos))
        pulados = 0
        total = 0                              # progresso, atravessa transacoes

        alvos = ([self.transacao] if self.transacao
                 else list(source.transactions))
        for transaction in alvos:
            if transaction not in source.transactions:
                log.warning("%s nao declara a transacao %r -- pulando",
                            source.domain, transaction)
                continue
            collected = 0                      # zera A CADA transacao
            for discovered in adapter.discover(source.domain, transaction):
                if budget is not None and collected >= budget:
                    log.warning("%s: teto de %d atingido em %r -- a coleta "
                                "desta transacao PAROU aqui e pode estar "
                                "incompleta", source.domain, budget, transaction)
                    break

                if conhecidos:
                    pid = derive_property_id(source.domain,
                                             discovered.listing_id, discovered.url)
                    if pid in conhecidos:
                        pulados += 1
                        if pulados % 500 == 0:
                            log.info("%s: %d ja conhecidos pulados",
                                     source.domain, pulados)
                        continue

                payload = adapter.fetch(discovered)
                if payload is None:
                    # 404/410 are ordinary here -- sitemap staleness is a
                    # platform characteristic (§11.1 item 5).
                    continue

                try:
                    raw = adapter.parse(payload)
                except PIIViolation:
                    raise
                except Exception as exc:
                    normalizer.stats.seen += 1
                    normalizer.stats.rejected[f"parse_error:{type(exc).__name__}"] += 1
                    log.debug("parse failed for %s: %s", discovered.url, exc)
                    continue

                if normalizer.normalize(raw, discovered) is not None:
                    collected += 1
                    total += 1
                    if total % PASSO == 0:
                        dt = time.time() - t0
                        if budget is None:
                            log.info("%s: %d anuncios (%s) em %.0f min "
                                     "(%.1fs cada, sem teto)",
                                     source.domain, total, transaction,
                                     dt / 60, dt / total)
                        else:
                            falta = (budget - collected) * dt / total
                            log.info("%s: %d/%d anuncios (%s) em %.0f min "
                                     "(%.1fs cada, restam ~%.0f min)",
                                     source.domain, collected, budget, transaction,
                                     dt / 60, dt / total, falta / 60)

    def _ja_coletados(self, source: Source, target: Target) -> set:
        """
        `property_id` ja coletados DESTE MES para esta fonte e esta cidade.

        Sem isto uma segunda corrida repete a primeira: o `discover()` percorre
        as facetas na mesma ordem determinista, e a deduplicacao do adapter e
        por corrida. A coleta de 2026-08-31 trouxe 3.000 anuncios; repetir o
        comando traria os MESMOS 3.000.

        O recorte e o MES, nao o lake inteiro, e a diferenca importa nos dois
        sentidos:

          · dentro do mes  -- pedir 1.000 com 2.000 ja coletados busca 1.000
            INEDITOS, chegando a 3.000. O que ja veio nao gasta cota.
          · entre meses    -- em outubro o mesmo anuncio volta a ser coletavel.
            Preco e disponibilidade mudam, e uma serie mensal e justamente o
            que falta para medir valorizacao.

        Nao le a base processada -- so o bruto, que e a fonte da verdade sobre
        o que ja foi buscado.
        """
        # Do GRAVADOR, nao do config: `--raw-dir` sobrescreve so o writer, e
        # ler `config.raw_dir` apontava para o GCS padrao. A busca nao achava
        # nada, devolvia zero conhecidos e a coleta "incremental" recoletava
        # tudo -- sem aviso. Pego em 2026-09-01 porque a linha de log que
        # deveria dizer "N ja no lake" nunca apareceu.
        destino = getattr(self.writer, "raw_dir", None) or self.config.raw_dir
        if not destino or destino.startswith("gs://"):
            if destino and destino.startswith("gs://"):
                log.warning("--novos-apenas ainda nao le do GCS (%s); "
                            "nada sera pulado", destino)
            return set()
        raiz = Path(destino)
        if not raiz.exists():
            log.warning("--novos-apenas: %s nao existe; nada sera pulado", raiz)
            return set()
        conhecidos: set = set()
        # O caminho e {uf}/{cidade}/{ano}/{mes}/{dia}/{plataforma}/{dominio}/.
        #
        # A cidade vem do ALVO EM CURSO, nunca do filtro `--city`. Sem `--city`
        # o filtro e None, e usar `*` fazia a coleta de Santos -- gravada
        # segundos antes -- bloquear a de Sao Paulo na mesma fonte: o segundo
        # alvo achava que ja tinha tudo e voltava com zero linhas, sem erro.
        cidade = schema.slugify(target.city)
        # A particao virou {ano}/{mes}/{dia} -- tres segmentos, nao um token
        # `2026-08-27`. O padrao antigo era `{self.mes}-*`, que casava o token
        # inteiro; contra a hierarquia nova ele nao casa NADA, e o modo de
        # falha e o caro: zero particoes encontradas devolve zero conhecidos,
        # a coleta "incremental" recoleta tudo, e nenhuma excecao e levantada.
        ano, mes_num = self.mes.split("-")
        padrao = (f"*/{cidade}/{ano}/{mes_num}/*/"
                  f"{source.platform}/{source.domain}/*.parquet")
        achados = list(raiz.glob(padrao))
        log.info("%s: %d particao(oes) de %s em %s",
                 source.domain, len(achados), self.mes, target.city)
        for caminho in achados:
            # `OSError` cobre arquivo corrompido ou ausente, que e ordinario.
            # `Exception` cobriria tambem erro de programacao -- e foi o que
            # aconteceu em 2026-09-01: um `NameError` virou aviso de "arquivo
            # ilegivel" e a funcao devolveu zero conhecidos em silencio, o que
            # faria a coleta incremental recoletar tudo sem ninguem notar.
            try:
                d = pd.read_parquet(caminho, columns=["property_id"])
            except (OSError, ValueError) as exc:
                log.warning("nao consegui ler %s (%s)", caminho, type(exc).__name__)
                continue
            conhecidos |= set(d.property_id.dropna())
        return conhecidos

    # -- the run -----------------------------------------------------------

    def run(self, platform: str | None = None) -> list[SourceResult]:
        sources = self.config.enabled_sources(platform)
        if not sources:
            log.warning("no enabled sources%s",
                        f" for platform {platform!r}" if platform else "")

        for source in sources:
            for target in self.config.targets:
                if self.cidade and target.city != self.cidade:
                    continue
                if not source.atende(target.city):
                    log.info("%s nao atende %s -- pulando",
                             source.domain, target.city)
                    continue
                log.info("collecting %s (%s) for %s-%s",
                         source.domain, source.platform, target.city, target.state)
                self.results.append(self.collect_source(source, target))
        return self.results

    def report(self) -> str:
        lines = ["", "=" * 72, "COLLECTION REPORT", "=" * 72]
        total = 0
        for r in self.results:
            head = f"{r.source.platform}:{r.source.domain} [{r.target.city}-{r.target.state}]"
            if r.skipped:
                lines.append(f"  SKIP  {head} -- {r.skipped}")
                continue
            if r.error:
                lines.append(f"  FAIL  {head} -- {r.error}")
            else:
                lines.append(f"  OK    {head} -- {len(r.records)} rows -> "
                             f"{r.path or '(dry run)'}")
            total += len(r.records)
            if r.fetch_stats:
                lines.append(f"        fetch: {r.fetch_stats.report()}")
            if r.stats:
                lines.extend("        " + ln for ln in r.stats.report().splitlines())
        lines += ["-" * 72, f"  TOTAL {total} rows from {len(self.results)} source runs",
                  "=" * 72, ""]
        return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect listings from local realty sites")
    parser.add_argument("--config", default="sources.yaml")
    parser.add_argument("--platform", help="restrict the run to one platform")
    parser.add_argument("--limit", type=int,
                        help="quantos anuncios tentar coletar POR TRANSACAO. "
                             "Sem isto, nao ha teto.")
    parser.add_argument("--tipo", help="restringe a coleta a um tipo "
                        "(ex: apartamento_residencial). Cruza o tipo com cada "
                        "faceta geografica -- a faceta de tipo sozinha nao "
                        "publica sub-faceta e caparia no teto de paginacao.")
    parser.add_argument("--city", help="coleta so este alvo (ex: 'sao paulo'). "
                        "Sem ele, roda todos os alvos do config -- cada um com "
                        "seu proprio orcamento e seu proprio recorte do mes.")
    parser.add_argument("--mes", help="mes de referencia AAAA-MM (padrao: o atual). "
                        "Tudo que ja foi coletado neste mes e pulado, entao "
                        "`--limit` conta anuncios INEDITOS. Coleta de meses "
                        "anteriores nao bloqueia: o mesmo imovel volta a ser "
                        "coletavel no mes seguinte.")
    parser.add_argument("--transaction", choices=("venda", "locacao"),
                        help="coleta so esta transacao. Sem ele, cada transacao "
                             "declarada pela fonte recebe o `--limit` inteiro.")
    parser.add_argument("--dry-run", action="store_true", help="collect and gate, write nothing")
    parser.add_argument("--raw-dir", help="override storage.raw_dir (local path or gs:// URI)")
    parser.add_argument("--write", action="store_true",
                        help="actually write, overriding execution.dry_run in the config")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)

    # --dry-run always wins over --write: the safe flag is never overridable
    # by the unsafe one, whatever order they are given in.
    dry_run = args.dry_run or (config.dry_run and not args.write)
    writer = lake.LakeWriter(
        raw_dir=args.raw_dir or config.raw_dir, dry_run=dry_run)
    collector = Collector(config, writer=writer, limit=args.limit,
                          transacao=args.transaction,
                          mes=args.mes,
                          cidade=args.city, tipo=args.tipo)

    try:
        results = collector.run(platform=args.platform)
    except PIIViolation as exc:
        # The one exception this codebase must never swallow (§2.5).
        print(f"\nPII GATE FAILED -- NOTHING WRITTEN\n{exc}\n", file=sys.stderr)
        return EXIT_PII

    print(collector.report())

    if not any(r.ok for r in results):
        print("collection produced no rows -- treating as failure (plan section 10)",
              file=sys.stderr)
        return EXIT_NOTHING_COLLECTED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
