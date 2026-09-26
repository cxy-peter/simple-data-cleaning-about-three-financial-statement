"""Reproducible task regressions; not a user study or measured LLM/RAG accuracy."""
from __future__ import annotations
import json
from pathlib import Path
from qiming.engine import Engine
from qiming.store import Store, Principal

CASES = [
    ('公牛2024年三表核对', 'completed', 'statement_review'),
    ('公牛2024年现金流分析', 'completed', 'cashflow'),
    ('民爆光电2024年应收和存货观察', 'completed', 'working_capital'),
    ('兆驰2024年经营会议简报', 'completed', 'meeting_brief'),
    ('公牛和民爆2024年对比', 'completed', 'compare'),
    ('晶丰明源2023年现金流', 'completed', 'cashflow'),
    ('晶丰明源2024年现金流', 'insufficient_data', 'cashflow'),
    ('公牛现金流分析', 'needs_clarification', 'cashflow'),
    ('2024年三表核对', 'needs_clarification', 'statement_review'),
    ('公牛和晶丰2024年对比', 'insufficient_data', 'compare'),
    ('公牛2023年三表核对', 'completed', 'statement_review'),
    ('公牛2025年三表核对', 'insufficient_data', 'statement_review'),
]

def run():
    store = Store()
    engine = Engine(store)
    rows = []
    for question, status, skill in CASES:
        result = engine.ask(Principal('regression'), question)
        ok = result['status'] == status and result['plan']['skill'] == skill
        rows.append({'question': question, 'expected_status': status,
                     'actual_status': result['status'], 'expected_skill': skill,
                     'actual_skill': result['plan']['skill'], 'pass': ok})
    output = {'mode': 'offline deterministic workflow', 'cases': len(rows),
              'passed': sum(row['pass'] for row in rows),
              'not_claimed': 'No live LLM/embedding evaluation or user outcome measurement.',
              'results': rows}
    store.close()
    return output

if __name__ == '__main__':
    report = run()
    target = Path('docs/task_regression.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] == report['cases'] else 1)
