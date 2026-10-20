import json
from pathlib import Path
import sys

rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
mode = sys.argv[2]
value_failures = {f'TestSQLiteValuesAllocation/{name}' for name in [
    'empty_with_maximum_limit', 'sparse_with_maximum_limit',
    'filtered_with_maximum_limit', 'grows_with_actual_results',
    'ignores_record_pagination',
]}
query_failures = {f'TestSQLiteQueryAllocation/{name}' for name in [
    'empty_with_maximum_batch', 'sparse_with_maximum_batch',
    'grows_to_actual_results', 'filters_with_maximum_batch',
    'offset_with_maximum_batch',
]}
expected = {'original': value_failures | query_failures, 'published': query_failures, 'fixed': set()}[mode]
cases = {row['Test']: row['Action'] for row in rows if '/' in row.get('Test', '')
         and row['Test'].startswith(('TestSQLiteValuesAllocation/', 'TestSQLiteQueryAllocation/'))
         and row['Action'] in ('pass', 'fail', 'skip')}
assert len(cases) == 16, cases
assert {name for name, status in cases.items() if status == 'fail'} == expected, cases
assert sum(status == 'pass' for status in cases.values()) == 16 - len(expected), cases
assert not any(row['Action'] in ('skip', 'build-fail') for row in rows), rows
output = ''.join(row.get('Output', '') for row in rows)
assert output.count('panicked: runtime error: makeslice: cap out of range') == len(expected), output
if mode == 'fixed':
    assert not any(row['Action'] == 'fail' for row in rows), rows
print(f'{mode}: {len(expected)} capacity panics, {16 - len(expected)} passing allocation cases; no build errors or skips.')
print('Passed test events (including parent groups):', sum(row['Action'] == 'pass' and 'Test' in row for row in rows))
