from pathlib import Path
import hashlib
import re
import subprocess

path = Path('app/models/shortened_label.rb')
fixed = path.read_bytes()
original = Path('original/shortened_label.rb').read_bytes()
def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
assert blob(original) == '34c176011b148dc7e7a39607e865bed255395a23'
assert blob(fixed) == '9a1953bf321a9f79ef81bc7f54e9b2a8f829f675'
expected_names = {
    'test_validation_preserves_a_persisted_short_code',
    'test_repeated_saves_preserve_the_published_short_code',
    'test_updating_the_label_keeps_the_existing_link',
    'test_failed_validation_does_not_replace_the_short_code',
    'test_custom_validation_context_keeps_the_short_code',
    'test_create_context_on_an_existing_record_keeps_the_short_code',
    'test_public_revert_resolves_the_original_code_after_a_save',
}
try:
    for label, source, expected in [('before', original, (13, 7, 0, 0)), ('after', fixed, (13, 0, 0, 0))]:
        path.write_bytes(source)
        result = subprocess.run(['bundle', 'exec', 'ruby', '-Itest', 'test/models/shortened_label_test.rb'],
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
            names = set(re.findall(r'ShortenedLabelTest#(test_[^\s\[]+)', output))
            assert names == expected_names, names
        print('Native %s verified: %d runs, %d failures, %d errors, %d skips' % ((label,) + counts))
finally:
    path.write_bytes(fixed)
assert path.read_bytes() == fixed
print('Fixed source restored byte-for-byte.')
