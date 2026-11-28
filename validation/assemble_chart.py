"""Assemble the tracked system_pm Helm inputs without loading any product images."""
import argparse
from pathlib import Path
import re
import shutil


def substitute(path, changes):
    value = path.read_text(encoding="utf-8")
    result = value
    for old, new in changes.items():
        result = result.replace(old, new)
    if result != value:
        path.write_text(result, encoding="utf-8", newline="\n")


def assemble(source, out, version):
    root = source / "src"
    shutil.copytree(root / "DPAProduct/build/helm/databackup", out)
    charts = out / "charts"
    pm = charts / "protect-manager"
    engine = charts / "protect-engine"
    infra = charts / "infrastructure"
    shutil.copytree(root / "ProtectManager/build/helm/protect-manager", pm)
    shutil.copytree(root / "Infrastructure_OM/ci/build/helm/protect-engine", engine)
    shutil.copytree(root / "Infrastructure_OM/ci/build/helm/infrastructure", infra)
    shutil.copytree(
        root / "ProtectManager/component/PM_API_Gateway/IngressRoute",
        pm / "templates", dirs_exist_ok=True,
    )
    shutil.copytree(engine / "conf", infra / "conf", dirs_exist_ok=True)
    substitute(infra / "templates/MultiClusterConf.yaml", {"d8": "d10"})
    substitute(infra / "templates/DigitalVersionConfig.yaml", {"current_digital_version": "800000001"})

    ports_config = (root / "Infrastructure_OM/ci/script/commParam.sh").read_text(encoding="utf-8")
    ports = dict(re.findall(r'^([a-z_]+_port)="(\d+)"$', ports_config, re.MULTILINE))
    expected = {
        "gaussdb_port", "kafka_port", "zookeeper_port", "redis_port",
        "elasticsearch_port", "om_port", "dmc_nginx_port",
    }
    if not expected <= ports.keys():
        raise ValueError("The upstream infrastructure port declarations changed.")
    substitute(infra / "values.yaml", {name: ports[name] for name in expected})
    for name in ("Chart.yaml", "values.yaml"):
        substitute(pm / name, {"\x24{NewAppVersion}": version})
    substitute(out / "values.yaml", {"product_version": version})
    for chart in (out, pm, engine, infra):
        metadata = chart / "Chart.yaml"
        exact = metadata.read_text(encoding="utf-8")
        exact = re.sub(r"^(version|appVersion):.*$", lambda match: f"{match[1]}: {version}",
                       exact, flags=re.MULTILINE)
        metadata.write_text(exact, encoding="utf-8", newline="\n")
        if chart != out:
            for manifest in chart.rglob("*.yaml"):
                substitute(manifest, {"{{ .Values.global.version }}": version})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    assemble(args.source, args.output, args.version)


if __name__ == "__main__":
    main()
