#!/bin/bash
set -euo pipefail

guide=$1
work=$2
cd "$work"
export KUBECONFIG="$work/kubeconfig"
cluster=oe-placement
cat > kind.yaml <<'YAML'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
YAML

cleanup() {
  local status=$?
  if (( status != 0 )); then
    kubectl get nodes --show-labels || true
    kubectl -n kube-system get daemonset,pods -o wide || true
    kubectl -n kube-system describe daemonset traefik-ingress-controller || true
    kubectl -n kube-system logs -l app=traefik --tail=45 || true
  fi
  kind delete cluster --name "$cluster"
}
trap cleanup EXIT
kind create cluster --name "$cluster" --wait 180s --config kind.yaml \
  --image kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f
cp="$cluster-control-plane"
kubectl get nodes -o wide
cp_ip="$(kubectl get node "$cp" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')"
test -n "$cp_ip"
test "$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o name)" = "node/$cp"
test -z "$(kubectl get nodes -l node-role.kubernetes.io/master -o name)"
test "$(kubectl get node "$cp" -o jsonpath='{.spec.taints[?(@.key=="node-role.kubernetes.io/control-plane")].effect}')" = NoSchedule

python3 "$GITHUB_WORKSPACE/validation/validation/prepare.py" "$guide" "$work/manifests" --node-ip "$cp_ip"
diff -q manifests/before/config.yaml manifests/after/config.yaml
diff -q manifests/before/rbac.yaml manifests/after/rbac.yaml
kubectl apply -k traefik-helm-chart-27.0.2/traefik/crds
kubectl wait --for=condition=Established --timeout=60s \
  crd/ingressroutes.traefik.containo.us crd/middlewares.traefik.containo.us \
  crd/serverstransports.traefik.containo.us crd/tlsoptions.traefik.containo.us crd/tlsstores.traefik.containo.us
kubectl apply -f manifests/before/config.yaml -f manifests/before/rbac.yaml
kubectl apply -f manifests/before/controller.yaml

ds=daemonset/traefik-ingress-controller
for _ in $(seq 1 30); do
  observed="$(kubectl -n kube-system get "$ds" -o jsonpath='{.status.observedGeneration}')"
  if test "$observed" = 1; then break; fi
  sleep 1
done
test "$observed" = 1
test "$(kubectl -n kube-system get "$ds" -o jsonpath='{.status.desiredNumberScheduled}')" = 0
echo 'Baseline guide scheduled 0 Traefik pods on the control-plane-only label layout'
kubectl apply -f manifests/after/controller.yaml
kubectl -n kube-system rollout status "$ds" --timeout=150s

check_placement() {
  test "$(kubectl -n kube-system get "$ds" -o jsonpath='{.status.desiredNumberScheduled},{.status.numberReady}')" = '1,1'
  test "$(kubectl -n kube-system get pods -l app=traefik -o jsonpath='{.items[*].spec.nodeName}')" = "$cp"
}
check_placement
echo 'Updated guide scheduled one ready Traefik pod on the control plane and none on the worker'

kubectl create namespace dpa
kubectl apply -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: routing-check
  namespace: dpa
spec:
  replicas: 1
  selector:
    matchLabels:
      app: routing-check
  template:
    metadata:
      labels:
        app: routing-check
    spec:
      containers:
        - image: busybox:1.37.0
          name: http
          command: ["/bin/sh", "-c"]
          args:
            - mkdir -p /srv/www && printf 'ebackup-routing-ok\n' >/srv/www/check && exec httpd -f -p 8080 -h /srv/www
          ports:
            - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: routing-check
  namespace: dpa
spec:
  selector:
    app: routing-check
  ports:
    - port: 8080
      targetPort: 8080
---
apiVersion: traefik.containo.us/v1alpha1
kind: IngressRoute
metadata:
  name: routing-check
  namespace: dpa
spec:
  entryPoints: [internal]
  routes:
    - kind: Rule
      match: Path("/check")
      services:
        - name: routing-check
          port: 8080
YAML
kubectl -n dpa rollout status deployment/routing-check --timeout=120s

check_route() {
  local reply
  for _ in $(seq 1 30); do
    reply="$(kubectl -n dpa exec deploy/routing-check -- wget -T 3 -qO- http://traefik.kube-system.svc.cluster.local/check 2>/dev/null || true)"
    if test "$reply" = 'ebackup-routing-ok'; then return; fi
    sleep 2
  done
  return 1
}
check_route
echo 'Real Traefik returned the legacy IngressRoute response'

kubectl label node "$cp" node-role.kubernetes.io/master= node-role.kubernetes.io/control-plane-
kubectl taint node "$cp" node-role.kubernetes.io/master:NoSchedule node-role.kubernetes.io/control-plane:NoSchedule-
kubectl -n kube-system rollout restart "$ds"
kubectl -n kube-system rollout status "$ds" --timeout=150s
check_placement
check_route
echo 'Updated guide also placed the controller and routed traffic with the legacy label and taint'
