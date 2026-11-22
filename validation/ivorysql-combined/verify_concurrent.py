"""Verify that concurrent Oracle regressions use their private Unix sockets."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import subprocess


def run(work, fixture, name):
    directory = work / name
    directory.mkdir()
    driver = Path("src/oracle_test/regress/pg_regress").resolve()
    prefix = Path(os.environ["IVORY_PREFIX"])
    result = subprocess.run(
        [
            str(driver), f"--bindir={prefix / 'bin'}", "--no-locale",
            f"--temp-instance={directory / 'instance'}",
            f"--inputdir={fixture}", f"--outputdir={directory}",
            "--port=58928", "connection",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
    )
    log = (directory / "log/postmaster.log").read_text()
    print(name, result.stdout, flush=True)
    if result.returncode or "Oracle TCP/IP" in log or ".s.PGSQL.58929" not in log:
        raise RuntimeError(log)


def main():
    work = Path(os.environ["RUNNER_TEMP"]) / "oracle-concurrent"
    fixture = work / "fixture"
    (fixture / "sql").mkdir(parents=True)
    (fixture / "expected").mkdir()
    message = "reached the Oracle regression connection"
    statement = r"\echo " + message + "\n"
    (fixture / "sql/connection.sql").write_text(statement, encoding="utf-8")
    (fixture / "expected/connection.out").write_text(
        statement + message + "\n", encoding="utf-8"
    )
    with socket.socket(socket.AF_INET) as ipv4, socket.socket(socket.AF_INET6) as ipv6:
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv4.bind(("127.0.0.1", 58929))
        ipv6.bind(("::1", 58929))
        ipv4.listen()
        ipv6.listen()
        with ThreadPoolExecutor(max_workers=2) as pool:
            checks = [pool.submit(run, work, fixture, name) for name in ("run-a", "run-b")]
            for check in checks:
                check.result()
    print("Both Oracle regression instances passed using private sockets.")


if __name__ == "__main__":
    main()
