"""HTML report generator.

Takes one or more run records, emits a self-contained HTML page with:
  - summary table (per-SUT means + count of questions)
  - per-question table with row coloring on regressions
  - inline rationales from the LLM judge

We use Jinja2 with a single inline template so the report is one file with
zero external assets — easy to email or attach to a PR.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from jinja2 import Template


_TEMPLATE = r"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>RAG Eval Report — {{ generated_at }}</title>
<style>
body{font-family:-apple-system,Segoe UI,sans-serif;margin:32px;color:#1d2129}
h1{margin:0 0 8px 0}
.muted{color:#6b7280}
table{border-collapse:collapse;margin:18px 0;width:100%}
th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left;font-size:13px;vertical-align:top}
th{background:#f3f4f6}
.metric-cell{text-align:right;font-variant-numeric:tabular-nums}
.bad{background:#fef2f2;color:#991b1b}
.warn{background:#fffbeb}
.ok{background:#f0fdf4}
.pill{font-size:11px;padding:2px 8px;border-radius:999px;background:#eef2ff;color:#3730a3;}
details{margin:8px 0}
summary{cursor:pointer;color:#374151}
pre{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:#f9fafb;padding:8px;border-radius:6px;white-space:pre-wrap;word-break:break-word}
</style>
</head>
<body>
<h1>RAG Regression Report</h1>
<div class="muted">Generated {{ generated_at }} — {{ runs|length }} run(s)</div>

<h2>Summary</h2>
<table>
<thead><tr>
  <th>SUT</th><th>n</th>
  <th class="metric-cell">clause_match</th>
  <th class="metric-cell">citation</th>
  <th class="metric-cell">context_recall</th>
  <th class="metric-cell">verbatim</th>
  <th class="metric-cell">faithfulness</th>
  <th class="metric-cell">relevancy</th>
  <th class="metric-cell">avg_ms</th>
  <th>regressions</th>
</tr></thead>
<tbody>
{% for run in runs %}
<tr>
  <td><b>{{ run.sut }}</b><div class="muted">{{ run.ts }}</div></td>
  <td>{{ run.n_questions }}</td>
  {% for k in ('clause_match','citation_correct','context_recall','answer_quotes_clause','faithfulness','answer_relevancy') %}
  <td class="metric-cell">{{ '%.2f'|format(run.summary[k]) if run.summary[k] is not none else '—' }}</td>
  {% endfor %}
  <td class="metric-cell">{{ '%.0f'|format(run.summary.avg_duration_ms) }}</td>
  <td>
  {% if run.regressions %}
    {% for r in run.regressions %}
    <span class="pill bad">{{ r.metric }} {{ '%+.2f'|format(r.delta) }}</span>
    {% endfor %}
  {% else %}
    <span class="pill ok">none</span>
  {% endif %}
  </td>
</tr>
{% endfor %}
</tbody>
</table>

{% for run in runs %}
<h2>{{ run.sut }} — per-question</h2>
<table>
<thead><tr>
  <th>qid</th><th>category</th><th>doc</th>
  <th class="metric-cell">clause</th>
  <th class="metric-cell">cite</th>
  <th class="metric-cell">recall</th>
  <th class="metric-cell">verbatim</th>
  <th class="metric-cell">faith</th>
  <th class="metric-cell">rel</th>
  <th>answer / rationale</th>
</tr></thead>
<tbody>
{% for r in run.rows %}
<tr class="{{ 'bad' if r.faithfulness is not none and r.faithfulness < 0.5 else '' }}">
  <td>{{ r.qid }}</td>
  <td>{{ r.category }}</td>
  <td>{{ r.doc_id }}</td>
  <td class="metric-cell">{{ '%.2f'|format(r.clause_match) }}</td>
  <td class="metric-cell">{{ '%.2f'|format(r.citation_correct) }}</td>
  <td class="metric-cell">{{ '%.2f'|format(r.context_recall) }}</td>
  <td class="metric-cell">{{ '%.2f'|format(r.answer_quotes_clause) }}</td>
  <td class="metric-cell">{{ '%.2f'|format(r.faithfulness) if r.faithfulness is not none else '—' }}</td>
  <td class="metric-cell">{{ '%.2f'|format(r.answer_relevancy) if r.answer_relevancy is not none else '—' }}</td>
  <td>
    <details>
      <summary>{{ (r.answer or '')[:120]|e }}{% if r.answer and r.answer|length > 120 %}…{% endif %}</summary>
      <pre>{{ r.answer|e }}</pre>
      {% if r.judge_rationale %}<div class="muted"><b>judge:</b> {{ r.judge_rationale|e }}</div>{% endif %}
      {% if r.error %}<div class="bad">error: {{ r.error|e }}</div>{% endif %}
    </details>
  </td>
</tr>
{% endfor %}
</tbody>
</table>
{% endfor %}
</body></html>
"""


def render_html(runs: list[dict[str, Any]], generated_at: str) -> str:
    return Template(_TEMPLATE).render(runs=runs, generated_at=generated_at)


def write_report(
    runs: list[dict[str, Any]],
    out_path: Path,
    *,
    generated_at: str,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(runs, generated_at), encoding="utf-8")
    return out_path
