"""Exercise actual Linux which/realpath behavior without a database cluster."""
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
import precheck
from common import global_thl_data

global_thl_data.logger = logging.getLogger('native-precheck')
results = []
with tempfile.TemporaryDirectory(prefix='otb-native-') as directory:
    root = Path(directory)
    home = root/'otb'
    sibling = root/'otb-old'
    outside = root/'outside'
    for folder in [home, sibling, outside]:
        (folder/'bin').mkdir(parents=True)
        binary = folder/'bin/postgres'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
    alias = root/'selected-home'
    alias.symlink_to(home, target_is_directory=True)
    linkbin = root/'linked-bin'
    linkbin.symlink_to(home/'bin', target_is_directory=True)
    escaping = home/'escape-bin'
    escaping.mkdir()
    (escaping/'postgres').symlink_to(outside/'bin/postgres')
    scenarios = [
        ('inside', home, home/'bin', 'postgres', True),
        ('prefix-sibling', home, sibling/'bin', 'postgres', False),
        ('unrelated', home, outside/'bin', 'postgres', False),
        ('escaping-symlink', home, escaping, 'postgres', False),
        ('selected-home-alias', alias, home/'bin', 'postgres', True),
        ('binary-directory-alias', home, linkbin, 'postgres', True),
        ('relative-home', os.path.relpath(home), home/'bin', 'postgres', True),
        ('missing', home, home/'bin', 'otb-precheck-nonexistent-fixture', False),
    ]
    for name, selected, bindir, executable, expected in scenarios:
        os.environ['OPENTENBASE_HOME'] = str(selected)
        os.environ['PATH'] = str(bindir) + ':/usr/bin:/bin'
        try:
            precheck.check_executable_in_OPENTENBASE_HOME(executable)
            accepted = True
        except SystemExit as error:
            assert error.code == 1
            accepted = False
        results.append({'name':name,'expected':expected,'accepted':accepted,'passed':accepted==expected})
print(json.dumps({'platform':sys.platform,'python':sys.version.split()[0],'scenarios':results,
                  'passed':sum(x['passed'] for x in results),'failed':sum(not x['passed'] for x in results)}))
raise SystemExit(any(not x['passed'] for x in results))
