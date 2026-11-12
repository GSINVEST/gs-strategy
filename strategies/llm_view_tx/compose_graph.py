"""Build ``graph.json`` by composition instead of by hand.

    python -m strategies.llm_view_tx.compose_graph            # rewrite graph.json
    python -m strategies.llm_view_tx.compose_graph --check    # fail if it drifted

The checked-in graph is exactly ``pipeline()``: every edge below exists because
two port names met with compatible types, and an incompatible wiring raises
while composing.  ``tests/test_compose_diagram.py`` asserts the file and the
expression agree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from strategies._common.compose.diagram import box

HERE = Path(__file__).parent
GRAPH = HERE / 'graph.json'
DEFAULT_MODEL = 'qwen3-235b-2507'


def pipeline(registry, model=DEFAULT_MODEL):
    def b(type_id, node_id, **params):
        return box(registry, type_id, node_id, **params)
    research = (b('data.quantdata_futures', 'data')
                >> b('feature.market_view', 'view')
                >> b('rag.research_context', 'docs')
                >> b('agent.llm_view', 'agent', model=model))
    evaluation = research >> b('backtest.walk_forward', 'backtest')
    diagnostics = b('pit.memorization_probe', 'probe', model=model)
    statistics = (b('ledger.selection_n', 'ledger')
                  >> (b('validation.report', 'report') @ b('stat.facts', 'facts'))
                  >> b('stat.resolve', 'resolve'))
    robustness = b('validation.monte_carlo', 'montecarlo')
    # The statistics block needs Returns from evaluation: compose, don't hand-wire.
    return evaluation >> diagnostics >> statistics >> robustness


def render(registry, model=DEFAULT_MODEL):
    graph = pipeline(registry, model).to_graph(registry, strategy='llm_view_tx')
    return json.dumps(graph, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def main(argv=None):
    from strategies._common.graph.service import default_registry
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    text = render(default_registry())
    if args.check:
        current = GRAPH.read_text(encoding='utf-8') if GRAPH.exists() else ''
        if current != text:
            print('graph.json differs from compose_graph.pipeline()', file=sys.stderr)
            return 1
        return 0
    GRAPH.write_text(text, encoding='utf-8', newline='\n')
    print(f'wrote {GRAPH.relative_to(HERE.parents[1])}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
