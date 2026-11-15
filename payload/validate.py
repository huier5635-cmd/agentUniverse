import concurrent.futures
import os
from pathlib import Path
import socket
import subprocess

work = Path(os.environ["RUNNER_TEMP"]) / "oracle-regress-3244"
work.mkdir()
source = Path.cwd().resolve()
driver = source / "src/oracle_test/regress/pg_regress"
prefix = Path(os.environ["IVORY_PREFIX"])
evidence = Path(os.environ["GITHUB_WORKSPACE"]) / "evidence"
evidence.mkdir(exist_ok=True)
fixtures = work / "fixtures"
(fixtures / "sql").mkdir(parents=True)
(fixtures / "expected").mkdir()
query = r"\echo reached the Oracle regression connection" + "\n"
(fixtures / "sql/connection.sql").write_text(query)
(fixtures / "expected/connection.out").write_text(query + "reached the Oracle regression connection\n")

def check(name):
    path = work / name
    path.mkdir()
    result = subprocess.run(
        [str(driver), "--bindir=" + str(prefix / "bin"), "--no-locale",
         "--temp-instance=" + str(path / "instance"), "--inputdir=" + str(fixtures),
         "--outputdir=" + str(path), "--port=58928", "connection"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
    )
    print(name, result.returncode, result.stdout, flush=True)
    (evidence / (name + ".log")).write_text(result.stdout)
    log = path / "log/postmaster.log"
    if log.exists():
        detail = log.read_text()
        (evidence / (name + "-postmaster.log")).write_text(detail)
    else:
        detail = ""
    return result.returncode, detail

with socket.socket(socket.AF_INET) as v4, socket.socket(socket.AF_INET6) as v6:
    v6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    v4.bind(("127.0.0.1", 58929))
    v6.bind(("::1", 58929))
    v4.listen()
    v6.listen()
    code, detail = check("original-occupied")
    assert code == 2 and "could not create any Oracle TCP/IP sockets" in detail
    subprocess.run(["git", "apply", str(source.parent / "payload/fix.patch")], check=True)
    subprocess.run(["make", "-s", "-C", "src/oracle_test/regress", "pg_regress"], check=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(check, ["fixed-a", "fixed-b"]))
    for code, detail in results:
        assert code == 0 and "Oracle TCP/IP" not in detail and ".s.PGSQL.58929" in detail

print("Original: reproducible Oracle TCP bind failure. Candidate: both concurrent suites succeeded through private Oracle sockets.")
