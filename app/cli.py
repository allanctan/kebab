"""KEBAB command-line interface.

Commands are wired progressively (M5 onward). Stubs print ``TODO: <stage>``
and exit 0 until their milestone lands.
"""

from __future__ import annotations

import click

from collections import Counter
from pathlib import Path

from app.config import env, setup_logging
from app.config.config import Settings
from app.core.llm.embeddings import embed
from app.core.store import Store
from app.agents.lint import agent as lint_agent
from app.agents.qa import qa as qa_agent
from app.agents import generate as generate_stage
from app.agents import organize as organize_stage
from app.agents import sync as sync_stage
from app.agents.ingest import pdf as pdf_ingest
from app.agents.ingest import web as web_ingest


@click.group()
@click.version_option()
def main() -> None:
    """KEBAB — Knowledge Engine for Building Authoritative Bases."""
    setup_logging()


# ---------- pipeline ----------


@main.group()
def ingest() -> None:
    """Ingest source material into knowledge/raw/."""


@ingest.command("pdf")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=True, file_okay=True, path_type=Path),
    help="Path to a single PDF file, or a folder to recurse into.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-process PDFs even if their processed/ output already exists. "
    "Use after changing filter thresholds or the describer prompt.",
)
def ingest_pdf(input_path: Path, force: bool) -> None:
    """Copy a PDF (or every PDF under a folder) into raw/ and synthesize processed/."""
    if input_path.is_dir():
        results = pdf_ingest.ingest_tree(env, input_path, force=force)
        total_chars = sum(r.chars for r in results)
        total_figures = sum(r.figure_count for r in results)
        total_described = sum(r.described_count for r in results)
        total_errors = sum(r.labeler_errors for r in results)
        error_note = f", {total_errors} labeler errors" if total_errors else ""
        click.echo(
            f"ingested {len(results)} PDF(s) from {input_path} "
            f"({total_chars} chars, {total_described}/{total_figures} figures described"
            f"{error_note})"
        )
        if total_errors:
            click.echo("  retry with: uv run kebab ingest retry-errors --stem <stem>")
    else:
        result = pdf_ingest.ingest(env, input_path, force=force)
        suffix = " (cached)" if result.skipped else ""
        error_note = (
            f" ({result.labeler_errors} labeler errors)" if result.labeler_errors else ""
        )
        click.echo(
            f"ingested {result.original.name}{suffix}: {result.chars} chars, "
            f"{result.described_count}/{result.figure_count} figures described"
            f"{error_note} → {result.text_path}"
        )
        if result.labeler_errors:
            stem = result.processed_dir.name
            click.echo(f"  retry with: uv run kebab ingest retry-errors --stem {stem}")


@ingest.command("retry-errors")
@click.option(
    "--stem",
    required=True,
    help="Processed document stem (e.g. 'SCI10_Q1_M2_Plate_Boundaries'). "
    "Matches the folder name under knowledge/processed/documents/.",
)
def ingest_retry_errors(stem: str) -> None:
    """Re-run the describer on figures that failed during a previous ingest."""
    result = pdf_ingest.retry_errors(env, stem)
    click.echo(
        f"retry-errors {stem}: retried {result.retried}, "
        f"recovered {result.recovered}, still failing {result.still_failing}"
    )



@ingest.command("web")
@click.option("--url", required=True)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-fetch the URL even if a cached copy already exists.",
)
def ingest_web_cmd(url: str, force: bool) -> None:
    """Fetch a web page and store raw HTML + cleaned text under raw/documents/."""
    result = web_ingest.ingest(env, url, force=force)
    suffix = " (cached)" if result.skipped else ""
    click.echo(f"ingested {url}{suffix} ({result.chars} chars → {result.text_path})")


@main.command()
@click.option("--domain", default="Knowledge", show_default=True, help="Top-level domain hint.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-propose the hierarchy even if a cached plan already exists.",
)
def organize(domain: str, force: bool) -> None:
    """Stage 1: propose (or load) the canonical hierarchy from knowledge/raw/."""
    result = organize_stage.run(env, domain_hint=domain, force=force)
    suffix = " (from cache)" if result.loaded_from_cache else ""
    click.echo(
        f"organize{suffix}: {len(result.plan.nodes)} nodes, "
        f"{len(result.created)} stubs created, {len(result.existing)} already existed "
        f"→ {result.plan_path}"
    )
    if result.extended_articles or result.added_articles:
        click.echo(
            f"  incremental: extended {len(result.extended_articles)} article(s), "
            f"added {len(result.added_articles)} new article(s)"
        )


@main.command()
@click.argument("article_id", required=False)
@click.option("--domain", default=None, help="Domain to generate for. Omit to run all domains.")
@click.option("--all", "generate_all", is_flag=True, help="Generate all articles in the domain.")
@click.option("--force", is_flag=True, default=False, help="Regenerate even if already written.")
def generate(article_id: str | None, domain: str | None, generate_all: bool, force: bool) -> None:
    """Find gaps, generate articles, classify contexts, write summaries."""
    from app.agents.organize import list_domains

    if article_id and not domain:
        # Single article — need to find which domain it's in
        domains = list_domains(env)
        if not domains:
            raise click.ClickException("no plans found — run `kebab organize --domain <name>` first")
        for d in domains:
            result = generate_stage.run(env, domain=d, article_id=article_id, force=True)
            if result.articles_written > 0:
                click.echo(
                    f"generate {article_id}: {result.articles_written} written, "
                    f"{result.contexts_updated} contexts"
                )
                return
        raise click.ClickException(f"article {article_id!r} not found in any plan")
    elif generate_all or (not article_id):
        # All articles in domain(s)
        if domain:
            domains = [domain]
        else:
            domains = list_domains(env)
            if not domains:
                raise click.ClickException("no plans found — run `kebab organize --domain <name>` first")
        for d in domains:
            click.echo(f"--- {d} ---")
            result = generate_stage.run(env, domain=d, force=force)
            click.echo(
                f"  {result.contexts_updated} contexts, {result.gaps_found} gaps, "
                f"{result.articles_written} written, {result.articles_skipped} skipped"
            )
    else:
        raise click.ClickException("provide an article ID or use --all")


@main.command()
def sync() -> None:
    """Stage 7: parse frontmatter, embed, and upsert to Qdrant."""
    result = sync_stage.run(env)
    click.echo(
        f"sync: indexed {result.articles} article(s); "
        f"confidence={result.confidence_histogram}; "
        f"skipped={len(result.skipped)}"
    )


# ---------- agents ----------


@main.command()
@click.argument("article_id", required=False)
@click.option("--all", "run_all", is_flag=True, help="Run on all articles.")
@click.option("--domain", default=None, help="Filter by domain folder name.")
@click.option("--once", is_flag=True, default=True, help="Run a single pass and exit.")
@click.option("--watch", is_flag=True, help="Run continuously.")
def qa(article_id: str | None, run_all: bool, domain: str | None, once: bool, watch: bool) -> None:
    """Q&A enrichment — generate grounded question-answer pairs."""
    if watch:
        once = False
    result = qa_agent.run(
        env,
        article_id=article_id if not run_all else None,
        domain=domain,
        once=once,
        watch=watch,
    )
    click.echo(
        f"qa: updated {len(result.updated)} article(s), "
        f"added {result.pairs_added} pair(s), skipped {len(result.skipped)}"
    )


def _iter_article_ids(domain: str | None) -> list[str]:
    """Return article IDs from curated markdown, optionally filtered by domain folder."""
    from app.core.markdown import read_article as _read_article

    curated = Path(env.CURATED_DIR)
    if not curated.exists():
        return []
    root = curated / domain if domain else curated
    if not root.exists():
        return []
    ids: list[str] = []
    for md in sorted(root.rglob("*.md")):
        try:
            fm, _, _ = _read_article(md)
        except Exception:  # noqa: BLE001
            continue
        ids.append(fm.id)
    return ids


@main.command()
@click.argument("article_id", required=False)
@click.option("--all", "run_all", is_flag=True, help="Research all articles.")
@click.option("--domain", default=None, help="Filter by domain folder name.")
@click.option("--budget", type=int, default=10, show_default=True, help="Max queries per article.")
def research(article_id: str | None, run_all: bool, domain: str | None, budget: int) -> None:
    """Verify an article's claims against external sources."""
    from app.agents.research import research as research_agent

    if run_all or domain:
        ids = _iter_article_ids(domain)
        if not ids:
            raise click.ClickException("no curated articles found")
        for aid in ids:
            result = research_agent.run(env, article_id=aid, budget=budget)
            click.echo(
                f"  {aid}: {result.confirms} confirmed, "
                f"{result.appends} appended, {result.disputes} disputed"
            )
    elif article_id:
        result = research_agent.run(env, article_id=article_id, budget=budget)
        click.echo(
            f"research {article_id}: {result.claims_total} claims, "
            f"{result.confirms} confirmed, {result.appends} appended, "
            f"{result.disputes} disputed"
        )
    else:
        raise click.ClickException("provide an article ID, --domain, or --all")


@main.command("research-gaps")
@click.argument("article_id", required=False)
@click.option("--all", "run_all", is_flag=True, help="Run on all articles.")
@click.option("--domain", default=None, help="Filter by domain folder name.")
@click.option("--budget", type=int, default=5, show_default=True, help="Max queries per article.")
def research_gaps(article_id: str | None, run_all: bool, domain: str | None, budget: int) -> None:
    """Answer unanswered questions in the Research Gaps section of an article."""
    from app.agents.research_gaps import research_gaps as gaps_agent

    if run_all or domain:
        ids = _iter_article_ids(domain)
        if not ids:
            raise click.ClickException("no curated articles found")
        for aid in ids:
            result = gaps_agent.run(env, article_id=aid, budget=budget)
            click.echo(f"  {aid}: {result.answered}/{result.gaps_total} gaps answered")
    elif article_id:
        result = gaps_agent.run(env, article_id=article_id, budget=budget)
        click.echo(
            f"research-gaps {article_id}: {result.answered}/{result.gaps_total} gaps answered"
        )
    else:
        raise click.ClickException("provide an article ID, --domain, or --all")


@main.command("research-images")
@click.argument("article_id", required=False)
@click.option("--all", "run_all", is_flag=True, help="Run on all articles.")
@click.option("--domain", default=None, help="Filter by domain folder name.")
def research_images(article_id: str | None, run_all: bool, domain: str | None) -> None:
    """Enrich an article with figures from its existing Wikipedia footnotes."""
    from app.agents.research_images import research_images as images_agent

    if run_all or domain:
        ids = _iter_article_ids(domain)
        if not ids:
            raise click.ClickException("no curated articles found")
        for aid in ids:
            result = images_agent.run(env, article_id=aid)
            click.echo(
                f"  {aid}: {result.images_added} added, "
                f"{result.decoratives_dropped} dropped, {result.targets_found} targets"
            )
    elif article_id:
        result = images_agent.run(env, article_id=article_id)
        click.echo(
            f"research-images {article_id}: {result.images_added} added, "
            f"{result.decoratives_dropped} dropped, {result.targets_found} targets"
        )
    else:
        raise click.ClickException("provide an article ID, --domain, or --all")


@main.command()
def lint() -> None:
    """Run all health checks and write a JSON report."""
    result = lint_agent.run(env)
    report = result.report
    click.echo(
        f"lint: scanned {report.articles_scanned} article(s), "
        f"found {len(report.issues)} issue(s) → {result.output_path}"
    )
    for code, count in sorted(report.counts.items()):
        click.echo(f"  {code}: {count}")


# ---------- utilities ----------


def _store(settings: Settings) -> Store:
    return Store(settings)


@main.command()
def status() -> None:
    """Show KB health at a glance."""
    store = _store(env)
    store.ensure_collection()
    total = store.count()
    histogram: Counter[int] = Counter()
    for article in store.scroll():
        histogram[article.confidence_level] += 1
    click.echo(f"Total articles: {total}")
    for level in sorted(histogram):
        marker = " (gate)" if level >= 3 else ""
        click.echo(f"  confidence {level}: {histogram[level]}{marker}")


@main.command()
@click.argument("query")
@click.option("--limit", type=int, default=10, show_default=True)
def search(query: str, limit: int) -> None:
    """Vector search the KB."""
    store = _store(env)
    store.ensure_collection()
    vector = embed(query, env)
    hits = store.search(vector, limit=limit)
    if not hits:
        click.echo("(no matches)")
        return
    for hit in hits:
        a = hit.article
        click.echo(
            f"  [{a.confidence_level}] {a.id}  {a.name}  ({hit.score:.3f})\n"
            f"      {a.description}"
        )


@main.command()
@click.argument("article_id")
def check(article_id: str) -> None:
    """Inspect a single article by ID."""
    store = _store(env)
    store.ensure_collection()
    matches = store.retrieve([article_id])
    if not matches:
        raise click.ClickException(f"no article with id {article_id!r}")
    a = matches[0]
    click.echo(f"id: {a.id}")
    click.echo(f"name: {a.name}")
    click.echo(f"description: {a.description}")
    click.echo(f"domain: {a.domain} / {a.subdomain}")
    click.echo(f"confidence: {a.confidence_level}")
    click.echo(f"keywords: {', '.join(a.keywords) if a.keywords else '(none)'}")
    if a.md_path:
        click.echo(f"markdown: {a.md_path}")


@main.command()
@click.argument("domain")
def tree(domain: str) -> None:
    """Print the hierarchy under DOMAIN."""
    store = _store(env)
    store.ensure_collection()
    articles = list(store.scroll(Store.domain_filter(domain)))
    if not articles:
        click.echo(f"(no articles in domain {domain!r})")
        return
    by_subdomain: dict[str | None, list] = {}
    for article in articles:
        by_subdomain.setdefault(article.subdomain, []).append(article)
    for subdomain in sorted(by_subdomain, key=lambda x: x or ""):
        click.echo(f"{domain} / {subdomain or '(none)'}")
        for article in sorted(by_subdomain[subdomain], key=lambda a: a.id):
            click.echo(f"  - {article.id}  {article.name}  [c{article.confidence_level}]")


@main.command("list")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option("--min-confidence", type=int, default=0, show_default=True, help="Minimum confidence level.")
@click.option("--sort", "sort_by", type=click.Choice(["id", "name", "confidence", "domain"]), default="id", show_default=True)
def list_articles(domain: str | None, min_confidence: int, sort_by: str) -> None:
    """List all indexed articles with key fields."""
    store = _store(env)
    store.ensure_collection()
    filt = Store.domain_filter(domain) if domain else None
    articles = list(store.scroll(filt))
    if not articles:
        click.echo("(no articles indexed)")
        return
    if min_confidence > 0:
        articles = [a for a in articles if a.confidence_level >= min_confidence]
        if not articles:
            click.echo(f"(no articles at confidence >= {min_confidence})")
            return

    sort_keys = {
        "id": lambda a: a.id,
        "name": lambda a: a.name,
        "confidence": lambda a: (-a.confidence_level, a.id),
        "domain": lambda a: (a.domain, a.subdomain or "", a.id),
    }
    articles.sort(key=sort_keys[sort_by])

    # Header
    click.echo(f"{'ID':<30s} {'Name':<40s} {'Conf':>4s}")
    click.echo("-" * 76)
    for a in articles:
        click.echo(f"{a.id:<30s} {a.name[:40]:<40s} {a.confidence_level:>4d}")
    click.echo(f"\n{len(articles)} article(s)")


# ---------- evals ----------


@main.group(name="eval")
def eval_group() -> None:
    """Run eval suites (pydantic-evals)."""


def _print_eval_summary(suite_name: str, aggregate: dict[str, float], output_path: Path) -> None:
    from evals.run import compare_to_baseline

    click.echo(f"{suite_name}: {output_path}")
    for metric, value in sorted(aggregate.items()):
        click.echo(f"  {metric}: {value}")
    check = compare_to_baseline(suite_name, aggregate)
    if check.passed:
        click.echo("  baseline: PASS")
    else:
        click.echo("  baseline: FAIL")
        for metric, observed, floor in check.failures:
            click.echo(f"    {metric}: {observed} < {floor}")
        raise click.ClickException("baseline check failed")


@eval_group.command("generation")
def eval_generation() -> None:
    """Run the generation eval suite."""
    from evals.suites import generation

    result = generation.run(env)
    _print_eval_summary("generation", result.aggregate, result.output_path)


@eval_group.command("verification")
def eval_verification() -> None:
    """Run the verification eval suite."""
    from evals.suites import verification

    result = verification.run(env)
    _print_eval_summary("verification", result.aggregate, result.output_path)


@eval_group.command("qa")
def eval_qa() -> None:
    """Run the Q&A eval suite."""
    from evals.suites import qa

    result = qa.run(env)
    _print_eval_summary("qa", result.aggregate, result.output_path)


@eval_group.command("figure-filter")
@click.option(
    "--include-unreviewed",
    is_flag=True,
    default=False,
    help="Score against LLM labels for entries that haven't been human-reviewed yet.",
)
def eval_figure_filter(include_unreviewed: bool) -> None:
    """Score the figure filter against reviewed ground truth (F1)."""
    from evals.suites import figure_filter

    result = figure_filter.run(env, include_unreviewed=include_unreviewed)
    click.echo(
        f"figure_filter: scored {int(result.aggregate['total'])} figures "
        f"(reviewed={result.reviewed_count}, unreviewed={result.unreviewed_count})"
    )
    if result.report.per_rule:
        click.echo("  per-rule precision:")
        for rule, cm in sorted(result.report.per_rule.items()):
            click.echo(
                f"    {rule:10s} tp={cm.true_positives:>4} fp={cm.false_positives:>4} "
                f"precision={cm.precision:.3f}"
            )
    if result.report.false_positives:
        click.echo(f"  {len(result.report.false_positives)} false positives (useful wrongly dropped)")
    if result.report.false_negatives:
        click.echo(f"  {len(result.report.false_negatives)} false negatives (decorative wrongly kept)")
    _print_eval_summary("figure_filter", result.aggregate, result.output_path)


if __name__ == "__main__":
    main()
