#!/usr/bin/env python3
"""
Data 360 CI Concordance
Builds a DMO / field / example-CI index from a client's on-disk CI SQL and
proposes candidate concept-map entries.

Usage:
    python3 ci_concordance.py build --output-dir <client-data360-folder>
    python3 ci_concordance.py propose --output-dir <client-data360-folder> --term <term>
    python3 ci_concordance.py propose --output-dir <client-data360-folder> --all

Read-only static analysis of <output-dir>/queries/*.sql. No org auth needed.
"""

import re
from collections import Counter
from pathlib import Path

# DMO name pattern — matches `ssot__Individual__dlm` and custom `Foo_Bar__dlm`.
_DMO_PAT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__dlm)\b")
# DMO.field references.
_FIELD_PAT = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*__dlm)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b"
)
# JOIN clauses — capture the DMO that follows the JOIN keyword.
_JOIN_PAT = re.compile(
    r"\b(?:INNER|LEFT|RIGHT|FULL)(?:\s+OUTER)?\s+JOIN\s+([A-Za-z_][A-Za-z0-9_]*__dlm)\b",
    re.IGNORECASE,
)

_RX_LINE_COMMENT = re.compile(r"--[^\n]*")
_RX_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RX_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")


def _strip_comments_and_strings(sql: str) -> str:
    """Remove comments and single-quoted string literals so downstream regex
    passes don't match inside them. Mirrors ci_audit's helper of the same name.
    """
    sql = _RX_BLOCK_COMMENT.sub(" ", sql)
    sql = _RX_LINE_COMMENT.sub(" ", sql)
    sql = _RX_STRING_LITERAL.sub("''", sql)
    return sql


def _extract_dmo_field_refs(sql: str):
    """Parse a CI SQL body. Returns (dmos, fields, joins).

    dmos   — set of DMO names referenced anywhere.
    fields — list of (dmo, field) tuples for every DMO.field reference.
    joins  — list of unordered DMO-pair tuples collected from FROM+JOIN
             sequences. Each pair is normalized alphabetically.
    """
    cleaned = _strip_comments_and_strings(sql)
    dmos = set(_DMO_PAT.findall(cleaned))
    fields = _FIELD_PAT.findall(cleaned)

    # Join pairs: collect every DMO in the FROM / JOIN chain of this statement,
    # then produce all unordered pair combinations. This is coarse but
    # sufficient for `top_join_partners` ranking — the design only asks for a
    # co-occurrence signal, not a graph.
    joined_dmos = list(_JOIN_PAT.findall(cleaned))
    # The FROM'd table also participates in joins with everything JOIN'd against it.
    from_pat = re.compile(
        r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*__dlm)\b", re.IGNORECASE
    )
    from_dmos = from_pat.findall(cleaned)
    participating = from_dmos + joined_dmos
    joins = []
    seen_pairs = set()
    for i, a in enumerate(participating):
        for b in participating[i + 1:]:
            if a == b:
                continue
            pair = tuple(sorted((a, b)))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                joins.append(pair)
    return dmos, fields, joins


try:
    import yaml
except ImportError:
    import sys
    print("ERROR: pyyaml not installed. Use the bundled .venv or `pip install pyyaml`.",
          file=sys.stderr)
    sys.exit(1)


def _load_concept_map(root: Path):
    """Read <root>/concept-map.yaml if present; return None otherwise."""
    path = root / "concept-map.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text()) or {}


def _write_dmo_usage_yaml(index: dict, out_path: Path) -> None:
    """Emit the YAML sidecar. Deterministic ordering: DMOs alphabetical, keys
    within each DMO in a fixed sequence."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {}
    for dmo in sorted(index):
        entry = index[dmo]
        ordered[dmo] = {
            "ci_fan_in": entry["ci_fan_in"],
            "top_fields": entry["top_fields"],
            "top_join_partners": entry["top_join_partners"],
            "example_cis": entry["example_cis"],
        }
    out_path.write_text(yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False))


def _write_dmo_usage_md(index: dict, out_path: Path, concept_map) -> None:
    """Human-readable rollup. Grouped: top-fan-in DMOs first, then a
    coverage-gap section listing DMOs referenced but absent from concept-map.yaml.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# DMO Usage", ""]
    lines.append(f"_{len(index)} DMO(s) referenced across the client's CIs._")
    lines.append("")
    lines.append("## DMOs by CI fan-in")
    lines.append("")
    lines.append("| DMO | CIs | Top fields | Top join partners |")
    lines.append("|---|---:|---|---|")
    ranked = sorted(
        index.items(),
        key=lambda kv: (-kv[1]["ci_fan_in"], kv[0]),
    )
    for dmo, entry in ranked:
        top_fields = ", ".join(
            f"`{f['name']}`({f['count']})" for f in entry["top_fields"][:5]
        ) or "—"
        top_partners = ", ".join(
            f"`{p['name']}`({p['count']})" for p in entry["top_join_partners"][:3]
        ) or "—"
        lines.append(
            f"| `{dmo}` | {entry['ci_fan_in']} CI | {top_fields} | {top_partners} |"
        )
    lines.append("")

    # Coverage gaps — DMOs referenced but not present in any concept-map.yaml entry.
    covered = set()
    if concept_map:
        for concept, block in concept_map.items():
            for d in (block or {}).get("dmos", []) or []:
                covered.add(d)
    gaps = [d for d in sorted(index) if d not in covered]
    lines.append("## Coverage gap")
    lines.append("")
    if not gaps:
        lines.append("_All referenced DMOs are covered by at least one `concept-map.yaml` entry._")
    else:
        lines.append(
            "_DMOs referenced by CIs but not present in any `concept-map.yaml` "
            "entry. Add a concept to cover them, or accept the gap._"
        )
        lines.append("")
        for d in gaps:
            lines.append(f"- `{d}` — {index[d]['ci_fan_in']} CI(s)")
    lines.append("")
    out_path.write_text("\n".join(lines))


def cmd_build(output_dir: Path) -> int:
    """Build subcommand — scan queries/*.sql and write the index + rollup."""
    output_dir = output_dir.expanduser()
    q_dir = output_dir / "queries"
    if not q_dir.is_dir():
        import sys
        print(f"ERROR: queries dir not found: {q_dir}", file=sys.stderr)
        return 1
    index = _build_index(q_dir)
    concept_map = _load_concept_map(output_dir)
    _write_dmo_usage_yaml(index, output_dir / "object-model" / "dmo-usage.yaml")
    _write_dmo_usage_md(index, output_dir / "object-model" / "dmo-usage.md", concept_map)
    print(
        f"Wrote {output_dir / 'object-model' / 'dmo-usage.yaml'}\n"
        f"Wrote {output_dir / 'object-model' / 'dmo-usage.md'}\n"
        f"  {len(index)} DMO(s) indexed"
    )
    return 0


def _build_index(queries_dir: Path) -> dict:
    """Aggregate DMO / field / join references across every *.sql under
    queries_dir. Returns a dict keyed by DMO name; each value has keys:
    - ci_fan_in (int): distinct CIs referencing this DMO
    - top_fields (list[dict{name, count}]): top 10 fields by CI count, sorted
      by -count then alphabetically by name
    - top_join_partners (list[dict{name, count}]): top 5 join partners by CI
      count, sorted by -count then alphabetically by name
    - example_cis (list[str]): up to 5 CI stems (file prefixes), sorted
      alphabetically for stability
    """
    dmo_ci_counts: dict[str, set] = {}          # dmo -> set of CI names
    dmo_field_counts: dict[str, Counter] = {}   # dmo -> Counter[field]
    dmo_join_counts: dict[str, Counter] = {}    # dmo -> Counter[partner_dmo]

    for sql_path in sorted(queries_dir.glob("*.sql")):
        ci_name = sql_path.stem
        sql = sql_path.read_text(errors="replace")
        dmos, fields, joins = _extract_dmo_field_refs(sql)

        for d in dmos:
            dmo_ci_counts.setdefault(d, set()).add(ci_name)

        # Field references — count once per (dmo, field, ci) so a field
        # referenced 4 times in one CI still contributes 1 to the CI-count
        # style ranking. Matches the design's "count of CIs projecting this
        # field" semantics.
        per_ci_fields = {(d, f) for (d, f) in fields}
        for (d, f) in per_ci_fields:
            dmo_field_counts.setdefault(d, Counter())[f] += 1

        # Join partners — count once per (dmo_a, dmo_b, ci).
        for (a, b) in joins:
            dmo_join_counts.setdefault(a, Counter())[b] += 1
            dmo_join_counts.setdefault(b, Counter())[a] += 1

    index: dict[str, dict] = {}
    for dmo, ci_set in dmo_ci_counts.items():
        cis = sorted(ci_set)
        fields_counter = dmo_field_counts.get(dmo, Counter())
        joins_counter = dmo_join_counts.get(dmo, Counter())
        index[dmo] = {
            "ci_fan_in": len(cis),
            "top_fields": [
                {"name": name, "count": count}
                for name, count in sorted(
                    fields_counter.items(), key=lambda kv: (-kv[1], kv[0])
                )[:10]
            ],
            "top_join_partners": [
                {"name": name, "count": count}
                for name, count in sorted(
                    joins_counter.items(), key=lambda kv: (-kv[1], kv[0])
                )[:5]
            ],
            "example_cis": cis[:5],
        }
    return index


_HEADING_RX = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_ALIAS_LINE_RX = re.compile(r"(?im)^.*?\baliases?\s*:\s*(.+?)$")


def _glossary_terms(glossary_path: Path):
    """Parse `## Heading` blocks from glossary.md. For each heading, look
    for a line like `Aliases: X, Y, Z` in the body and collect the aliases.
    Returns [(term, aliases_list), ...]. Empty list if no glossary."""
    if not glossary_path.exists():
        return []
    text = glossary_path.read_text()
    result = []
    # Split on headings; the split gives [preamble, term1, body1, term2, body2, ...].
    parts = _HEADING_RX.split(text)
    # parts[0] is the preamble. Then term / body pairs.
    for i in range(1, len(parts), 2):
        term = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        aliases: list[str] = []
        for m in _ALIAS_LINE_RX.finditer(body):
            for a in m.group(1).split(","):
                alias = a.strip().rstrip(".")
                if alias:
                    aliases.append(alias)
        result.append((term, aliases))
    return result


def _tokenize_filename(stem: str) -> list[str]:
    """Split a CI filename stem into lowercase tokens for matching."""
    # Strip the __cio suffix, then split on underscore. Match against tokens
    # AND against consecutive-token combinations (so "Market Coverage" hits
    # "Market_Coverage__cio.sql").
    base = stem.replace("__cio", "")
    return [t.lower() for t in base.split("_") if t]


def _match_cis_for_term(queries_dir: Path, term: str, aliases):
    """Return CI SQL paths whose filename tokens match the term or any alias.

    Match rules: case-insensitive; matches either a single token, or a run of
    consecutive tokens joined by underscores. So "Market Coverage" (alias
    "Market_Coverage") matches Market_Coverage__cio.sql; "SalesFcst" matches SalesFcst_*.sql; and an
    alias "SalesFcst" matches SalesFcst_Throughput__cio.sql.
    """
    needles = {term.lower().replace(" ", "_")}
    for a in aliases:
        needles.add(a.lower().replace(" ", "_"))

    hits = []
    for sql_path in sorted(queries_dir.glob("*.sql")):
        tokens = _tokenize_filename(sql_path.stem)
        # Build the set of contiguous-token joins for this filename.
        joined_windows = set()
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens) + 1):
                joined_windows.add("_".join(tokens[i:j]))
        if joined_windows & needles:
            hits.append(sql_path)
    return hits


def _propose_for_term(queries_dir: Path, term: str, aliases, min_cis: int = 3):
    """Return a proposal dict for `term` or None if fewer than min_cis matched."""
    matches = _match_cis_for_term(queries_dir, term, aliases)
    if len(matches) < min_cis:
        return None

    dmo_ci_count: Counter = Counter()
    field_ci_count: dict[str, Counter] = {}
    for sql_path in matches:
        sql = sql_path.read_text(errors="replace")
        dmos, fields, _ = _extract_dmo_field_refs(sql)
        for d in dmos:
            dmo_ci_count[d] += 1
        seen_fields = set()
        for (d, f) in fields:
            if (d, f) not in seen_fields:
                seen_fields.add((d, f))
                field_ci_count.setdefault(d, Counter())[f] += 1

    n_matched = len(matches)
    # DMOs referenced by ≥25% of matched CIs.
    dmo_threshold = max(1, int(round(0.25 * n_matched)))
    ranked_dmos = [
        {"name": d, "count": c}
        for d, c in sorted(
            dmo_ci_count.items(), key=lambda kv: (-kv[1], kv[0])
        )
        if c >= dmo_threshold
    ]

    # Fields projected by ≥50% of matched CIs, across all proposed DMOs.
    field_threshold = max(1, int(round(0.50 * n_matched)))
    key_fields: list[str] = []
    seen = set()
    for dmo_entry in ranked_dmos:
        for f, c in (field_ci_count.get(dmo_entry["name"], Counter())).most_common():
            if c >= field_threshold and f not in seen:
                seen.add(f)
                key_fields.append(f)

    return {
        "term": term,
        "aliases": list(aliases),
        "matched_cis": [p.stem for p in matches],
        "dmos": ranked_dmos,
        "key_fields": key_fields,
        "rationale": (
            f"{n_matched} CI(s) matched by filename token / alias. "
            f"DMOs above ≥{dmo_threshold} CIs shown; "
            f"key_fields projected by ≥{field_threshold} matched CIs."
        ),
    }


def _format_proposal_yaml(proposal: dict) -> str:
    """Emit a paste-ready YAML block with rationale as leading comments.
    Comment lines are prefixed with `# ` so they survive yaml.safe_load
    (they are stripped on parse) but remain human-readable in the file.
    """
    lines = [f"# Proposed by ci_concordance: {proposal['rationale']}"]
    lines.append(f"# Matched CIs: {', '.join(proposal['matched_cis'])}")
    lines.append(f"{proposal['term']}:")
    if proposal["aliases"]:
        aliases_yaml = ", ".join(proposal["aliases"])
        lines.append(f"  aliases: [{aliases_yaml}]")
    lines.append("  dmos:")
    for d in proposal["dmos"]:
        lines.append(f"    - {d['name']}   # {d['count']} CI(s)")
    if proposal["key_fields"]:
        lines.append(
            f"  key_fields: [{', '.join(proposal['key_fields'])}]"
        )
    lines.append("  notes: \"\"")
    return "\n".join(lines) + "\n"


def cmd_propose(output_dir: Path, term, all_flag: bool) -> int:
    """Propose subcommand driver."""
    output_dir = output_dir.expanduser()
    q_dir = output_dir / "queries"
    if not q_dir.is_dir():
        import sys
        print(f"ERROR: queries dir not found: {q_dir}", file=sys.stderr)
        return 1

    if all_flag:
        terms = _glossary_terms(output_dir / "glossary.md")
        if not terms:
            # Fallback: use filename-token clusters. Take the first token of
            # each filename (case-preserved) and dedupe.
            clusters = set()
            for sql_path in q_dir.glob("*.sql"):
                first = sql_path.stem.split("_")[0]
                if first and not first.endswith("cio"):
                    clusters.add(first)
            terms = [(c, []) for c in sorted(clusters)]
        emitted = 0
        for term_name, aliases in terms:
            proposal = _propose_for_term(q_dir, term_name, aliases)
            if proposal is not None:
                print(_format_proposal_yaml(proposal))
                emitted += 1
        import sys
        print(f"# {emitted} proposal(s) emitted", file=sys.stderr)
        return 0

    if not term:
        import sys
        print("ERROR: propose requires --term <T> or --all", file=sys.stderr)
        return 2

    # Single-term mode. Try to pick up aliases from the glossary if present.
    aliases: list[str] = []
    for gterm, galiases in _glossary_terms(output_dir / "glossary.md"):
        if gterm.lower() == term.lower():
            aliases = galiases
            break

    proposal = _propose_for_term(q_dir, term, aliases)
    if proposal is None:
        import sys
        print(
            f"No proposal — fewer than 3 CIs matched the term '{term}' "
            f"(with aliases {aliases}).",
            file=sys.stderr,
        )
        return 0
    print(_format_proposal_yaml(proposal))
    return 0


import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a DMO/field/example-CI index from a client's CI SQL, or "
            "propose candidate concept-map.yaml entries."
        ),
        epilog=(
            "Pass --output-dir <client-data360-folder> — the script reads "
            "<root>/queries/*.sql and writes <root>/object-model/dmo-usage.{yaml,md}. "
            "For propose, pass --term <T> for a single term or --all to walk every "
            "glossary heading."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Scan queries/, write dmo-usage.{yaml,md}.")
    p_build.add_argument("--output-dir", required=True,
                         help="Client Data360 root (parent of queries/ and object-model/).")

    p_propose = sub.add_parser("propose", help="Emit candidate concept-map.yaml block(s).")
    p_propose.add_argument("--output-dir", required=True,
                           help="Client Data360 root.")
    grp = p_propose.add_mutually_exclusive_group(required=True)
    grp.add_argument("--term", help="Business concept to propose an entry for.")
    grp.add_argument("--all", dest="all_flag", action="store_true",
                     help="Walk every glossary heading (or filename cluster) and emit "
                          "proposals for terms with ≥3 matched CIs.")

    args = parser.parse_args(argv)

    if args.cmd == "build":
        return cmd_build(Path(args.output_dir))
    if args.cmd == "propose":
        return cmd_propose(
            Path(args.output_dir),
            term=args.term,
            all_flag=args.all_flag,
        )
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
