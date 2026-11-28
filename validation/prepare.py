"""Build the guide change and extract its Traefik Kubernetes examples."""
import argparse
from difflib import unified_diff
from pathlib import Path
import re
from textwrap import dedent


def candidate(original):
    marker = "4.  编辑traefik\\_ingress\\_controller.yaml\n\n"
    addition = (
        "    下列清单兼容控制平面节点的 \x60node-role.kubernetes.io/control-plane\x60 和旧版 "
        "\x60node-role.kubernetes.io/master\x60 标签，并容忍对应的污点。\n\n"
    )
    affinity = "                      - key: node-role.kubernetes.io/master\n                        operator: Exists\n"
    modern_affinity = (
        "                  - matchExpressions:\n"
        "                      - key: node-role.kubernetes.io/control-plane\n"
        "                        operator: Exists\n"
    )
    toleration = "            - key: node-role.kubernetes.io/master\n              operator: Exists\n"
    modern_toleration = "            - key: node-role.kubernetes.io/control-plane\n              operator: Exists\n"
    assert original.count(marker) == original.count(affinity) == original.count(toleration) == 1
    return (original.replace(marker, marker + addition)
            .replace(affinity, affinity + modern_affinity)
            .replace(toleration, toleration + modern_toleration)
            .replace("tolerations:              ## 设置容忍所有污点，防止节点被设置污点",
                     "tolerations:              ## 容忍控制平面节点的污点"))


def extract(markdown, node_ip, output):
    section = markdown.split("##### Traefik安装", 1)[1].split("#### 安装软件包", 1)[0]
    fences = [dedent(block) for block in re.findall(r"(?m)^    [\x60]{3}[^\n]*\n([\s\S]*?)^    [\x60]{3}$", section)]
    markers = {
        "config.yaml": "kind: ConfigMap",
        "rbac.yaml": "kind: ClusterRole",
        "controller.yaml": "kind: DaemonSet",
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, marker in markers.items():
        [block] = [text for text in fences if marker in text]
        (output / name).write_text(block.replace("<IP_ADDR>", node_ip), encoding="utf-8", newline="\n")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("original", type=Path)
    cli.add_argument("output", type=Path)
    cli.add_argument("--node-ip")
    args = cli.parse_args()
    original = args.original.read_text(encoding="utf-8")
    edited = candidate(original)
    args.output.mkdir(parents=True, exist_ok=True)
    path = "doc/quick_guide/快速入门.md"
    patch = "".join(unified_diff(
        original.splitlines(keepends=True),
        edited.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    (args.output / "quick-start.md").write_text(edited, encoding="utf-8", newline="\n")
    (args.output / "traefik-control-plane.patch").write_text(patch, encoding="utf-8", newline="\n")
    if args.node_ip:
        extract(original, args.node_ip, args.output / "before")
        extract(edited, args.node_ip, args.output / "after")


if __name__ == "__main__":
    main()
