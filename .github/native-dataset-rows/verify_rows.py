from pathlib import Path
import hashlib
import re
import subprocess

path = Path('app/graphql/mutations/create_lab_dataset.rb')
fixed = path.read_bytes()
original = Path('original/create_lab_dataset.rb').read_bytes()
def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
assert blob(original) == '97cf75428941e664bd2c9b4f8852e07150b2e4d1'
assert blob(fixed) == 'c0a74cf75e042014757aaa632c02d9918ff6300e'
expected_names = {
    'test_merge_preserves_all_legacy_string_rows',
    'test_existing_legacy_string_is_not_new',
    'test_new_rows_are_stored_with_normalized_urls',
    'test_equivalent_new_url_does_not_schedule_an_existing_repo',
    'test_equivalent_new_url_does_not_duplicate_a_stored_row',
    'test_existing_url_is_normalized_before_matching',
    'test_remaining_new_rows_have_the_same_normalized_hash_shape',
    'test_equivalent_new_rows_schedule_only_one_analysis',
    'test_equivalent_new_rows_merge_once_and_keep_first_metadata',
    'test_empty_batch_raises_the_dataset_validation_error',
    'test_oversized_batch_raises_the_dataset_validation_error',
    'test_raw_batch_limit_is_checked_before_deduplication',
    'test_merged_legacy_rows_remain_readable_through_the_real_model',
}
try:
    for label, source, expected in [('before', original, (22, 13, 0, 0)), ('after', fixed, (22, 0, 0, 0))]:
        path.write_bytes(source)
        result = subprocess.run(['bundle', 'exec', 'ruby', '-Itest', 'test/models/lab_dataset_rows_test.rb'],
                                capture_output=True, text=True)
        output = result.stdout + result.stderr
        Path(label + '.txt').write_text(output, encoding='utf-8')
        print(output)
        summary = re.search(r'(\d+) runs, \d+ assertions, (\d+) failures, (\d+) errors, (\d+) skips', output)
        assert summary is not None, output
        counts = tuple(map(int, summary.groups()))
        assert counts == expected, (label, counts, expected)
        assert result.returncode == (1 if label == 'before' else 0), (label, result.returncode)
        if label == 'before':
            names = set(re.findall(r'LabDatasetRowsTest#(test_[^\s\[]+)', output))
            assert names == expected_names, names
        print('Native %s verified: %d runs, %d failures, %d errors, %d skips' % ((label,) + counts))
finally:
    path.write_bytes(fixed)
assert path.read_bytes() == fixed
print('Fixed source restored byte-for-byte.')
