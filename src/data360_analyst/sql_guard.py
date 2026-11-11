"""Keep customer rows out of the conversation.

The live SQL tools only return results that are aggregates: every output column of the
top-level SELECT is a summarising aggregate (COUNT, SUM, AVG, APPROX_COUNT_DISTINCT, STDDEV,
VARIANCE; MIN and MAX only over date or numeric fields) or a literal. Anything that can return a field value per record or per group (a bare
column, SELECT *, a GROUP BY key in the output, a window function) is refused, and the
caller gets the SQL to run in Query Editor instead, where the rows stay in the org.

The check fails closed: SQL that doesn't parse is treated as row-level.
"""

import re

import sqlglot
from sqlglot import exp

QUERY_EDITOR_HINT = (
    "This query returns record-level values, which can be customer data, so it isn't run here. "
    "Run it yourself in Data Cloud > Query Editor; the rows stay in the org. "
    "To get an answer here, ask for counts or totals instead of the records."
)


# Aggregates that summarise many records into a number. MIN and MAX are left out on purpose: they
# return one record's actual value (MAX(email) is an email), so they're allowed only over a field
# whose name says it's a date or a number (_MIN_MAX_OK). COLLECT_LIST, FIRST, ANY_VALUE and the
# like aren't listed, so they're refused.
_SUMMARY_AGGS = (exp.Count, exp.Sum, exp.Avg, exp.ApproxDistinct, exp.Stddev, exp.StddevPop,
                 exp.StddevSamp, exp.Variance, exp.VariancePop)
_MIN_MAX_OK = re.compile(r"(date|time|amount|total|quantity|qty|count|number|score|price|points)(__c)?$", re.I)


def _covered(c: exp.Expression) -> bool:
    """True when this column reference only reaches the output through a summarising aggregate."""
    agg = c.find_ancestor(exp.AggFunc)
    while agg is not None:
        if isinstance(agg, _SUMMARY_AGGS):
            return True
        if isinstance(agg, (exp.Min, exp.Max)):
            return isinstance(c, exp.Column) and bool(_MIN_MAX_OK.search(c.name or ""))
        agg = agg.find_ancestor(exp.AggFunc)
    return False


def _row_level_reason(select: exp.Select) -> str | None:
    for col in select.expressions:
        node = col.this if isinstance(col, exp.Alias) else col
        if node.find(exp.Window):
            return f"`{col.sql(dialect='spark')}` is a window function, which returns one value per row"
        if isinstance(node, exp.Star) or (isinstance(node, exp.Column) and isinstance(node.this, exp.Star)):
            return "SELECT * returns every field of every record"
        for c in node.find_all(exp.Column, exp.Star):
            if not _covered(c):
                return f"`{col.sql(dialect='spark')}` returns a field value, not a count or total"
    return None


def check(sql: str) -> str | None:
    """Return None when the SQL only returns aggregates, else the reason it's refused."""
    try:
        tree = sqlglot.parse_one(sql, read="spark")
    except Exception as e:  # sqlglot raises several error types; any parse failure fails closed
        return f"the SQL couldn't be parsed ({type(e).__name__}), so it can't be confirmed aggregate-only"
    if tree is None:
        return "no SQL statement found"
    selects = [tree] if isinstance(tree, exp.Select) else list(tree.find_all(exp.Select))
    if isinstance(tree, exp.SetOperation):
        selects = [s for s in selects if not s.find_ancestor(exp.Subquery)]
    if not isinstance(tree, (exp.Select, exp.SetOperation)) or not selects:
        return "only SELECT queries can be checked"
    for s in selects:
        reason = _row_level_reason(s)
        if reason:
            return reason
    return None


def refusal(sql: str, reason: str) -> dict:
    """The tool response for a refused query: why, and the SQL to run in Query Editor."""
    return {
        "_blocked": "customer_data",
        "reason": reason,
        "message": QUERY_EDITOR_HINT,
        "query_editor_sql": sql.strip(),
    }
