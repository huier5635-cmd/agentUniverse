"""Select the real Traefik resources from the rendered MasterServer chart."""
from collections import Counter
from pathlib import Path
import sys

import yaml


rendered, gateway = map(Path, sys.argv[1:])
documents = [item for item in yaml.safe_load_all(rendered.read_text(encoding="utf-8")) if item]
legacy = [item for item in documents if item["apiVersion"] == "traefik.containo.us/v1alpha1"]
counts = Counter(item["kind"] for item in legacy)
assert counts == {
    "IngressRoute": 31, "Middleware": 7, "ServersTransport": 1, "TLSOption": 2, "TLSStore": 1,
}, counts
names = {(item["kind"], item["metadata"]["namespace"], item["metadata"]["name"]) for item in legacy}
assert len(names) == len(legacy) and {name[1] for name in names} == {"dpa"}
assert any(
    item["kind"] == "PodSecurityPolicy" and item["apiVersion"] == "policy/v1beta1"
    for item in documents
)
print(f"Rendered {len(documents)} resources including {len(legacy)} distinct legacy Traefik objects.")
print("Legacy object types:", dict(sorted(counts.items())))
gateway.write_text(yaml.safe_dump_all(legacy, sort_keys=False), encoding="utf-8")
