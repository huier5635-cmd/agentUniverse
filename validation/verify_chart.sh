#!/bin/bash
set -euo pipefail
root=$1
cd "$root"
export KUBECONFIG="$root/kubeconfig"
chart_name=oe-master-chart
trap 'kind delete cluster --name "$chart_name"' EXIT
kind create cluster --name "$chart_name" --wait 175s \
  --image kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f
kubectl version
helm version --short
kubectl create namespace dpa
[[ "$(kubectl get crd -o name)" != *traefik.containo.us* ]]

python3 "$GITHUB_WORKSPACE/validation/assemble_chart.py" source assembled
helm package assembled --destination packages
chart=packages/databackup-1.0.0.tgz
test -s "$chart"
fixture_pwd="$(printf '%s' chart-fixture-only | base64 -w0)"
flags=(--namespace dpa --set global.replicas=1 --set global.deploy_type=d10 \
  --set-string "global.gaussdbpwd=$fixture_pwd")
helm template master-server "$chart" --kube-version v1.32.2 "${flags[@]}" > rendered.yaml
"$root/venv/bin/python" "$GITHUB_WORKSPACE/validation/extract_gateway.py" rendered.yaml gateway.yaml

if kubectl apply -n dpa --dry-run=server --validate=strict -f gateway.yaml > before.log 2>&1; then
  echo "Unexpected success without the Traefik CRDs."
  exit 1
fi
grep -m 1 -F 'no matches for kind "IngressRoute" in version "traefik.containo.us/v1alpha1"' before.log
kubectl apply -k traefik-helm-chart-27.0.2/traefik/crds
kubectl wait --for=condition=Established --timeout=60s \
  crd/ingressroutes.traefik.containo.us crd/middlewares.traefik.containo.us \
  crd/serverstransports.traefik.containo.us crd/tlsoptions.traefik.containo.us crd/tlsstores.traefik.containo.us
kubectl apply -n dpa --dry-run=server --validate=strict -f gateway.yaml -o name | tee after.log
test "$(wc -l < after.log)" -eq 42
echo 'Kubernetes accepted all 42 actual gateway objects after establishing the CRDs.'

if helm install master-server "$chart" "${flags[@]}" --dry-run=server --hide-secret > full-install.log 2>&1; then
  echo 'Unexpected whole-chart preflight success; reassess the PodSecurityPolicy gate.'
  exit 1
fi
grep -m 1 -F 'no matches for kind "PodSecurityPolicy" in version "policy/v1beta1"' full-install.log
if grep -F 'no matches for kind "IngressRoute"' full-install.log; then exit 1; fi
echo 'Whole-chart preflight still requires a Kubernetes-compatible PodSecurityPolicy migration.'
