#!/usr/bin/env bash
# Build the LakePlan image, install the Helm chart on a local kind cluster, and check it works.
#
#   deploy/kind/smoke-test.sh
#
# Needs docker, kind, kubectl and helm. Reuses the kind cluster named $CLUSTER if it
# exists, otherwise creates it. Set DELETE_CLUSTER=1 to remove it at the end.
#
# No LLM credentials needed: the pod gets a placeholder Databricks token, which is enough
# to start. The LLM is only called when someone chats.
set -euo pipefail

CLUSTER="${CLUSTER:-lakeplan}"
NAMESPACE="${NAMESPACE:-lakeplan}"
RELEASE="${RELEASE:-lakeplan}"
LOCAL_PORT="${LOCAL_PORT:-18000}"
IMAGE_REPO="lakeplan"
IMAGE_TAG="smoke-test"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CTX="kind-$CLUSTER"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mFAIL: %s\033[0m\n' "$*"; exit 1; }

cleanup() {
  [[ -n "${PF_PID:-}" ]] && kill "$PF_PID" 2>/dev/null || true
  if [[ "${DELETE_CLUSTER:-0}" == "1" ]]; then kind delete cluster --name "$CLUSTER"; fi
}
trap cleanup EXIT

step "kind cluster '$CLUSTER'"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "reusing existing cluster"
else
  kind create cluster --name "$CLUSTER" --wait 120s
fi

step "Build $IMAGE_REPO:$IMAGE_TAG and load it into kind"
docker build -t "$IMAGE_REPO:$IMAGE_TAG" "$ROOT"
kind load docker-image "$IMAGE_REPO:$IMAGE_TAG" --name "$CLUSTER"

step "helm lint"
helm lint "$ROOT/deploy/helm/lakeplan" --strict

step "helm install"
helm upgrade --install "$RELEASE" "$ROOT/deploy/helm/lakeplan" \
  --kube-context "$CTX" --namespace "$NAMESPACE" --create-namespace \
  --set image.repository="$IMAGE_REPO" \
  --set image.tag="$IMAGE_TAG" \
  --set image.pullPolicy=Never \
  --set secrets.DATABRICKS_TOKEN=placeholder-not-a-real-token \
  --wait --timeout 3m

step "helm test (health endpoint + web UI from inside the cluster)"
helm test "$RELEASE" --kube-context "$CTX" --namespace "$NAMESPACE" --logs

step "Pod hardening is in effect"
APP="$(kubectl --context "$CTX" -n "$NAMESPACE" get deploy -l "app.kubernetes.io/instance=$RELEASE" -o name)"
uid="$(kubectl --context "$CTX" -n "$NAMESPACE" exec "$APP" -- id -u)"
[[ "$uid" == "10001" ]] || fail "expected UID 10001, got $uid"
echo "runs as UID $uid"
if kubectl --context "$CTX" -n "$NAMESPACE" exec "$APP" -- touch /app/write-test 2>/dev/null; then
  fail "root filesystem is writable"
fi
echo "root filesystem is read-only"

step "Port-forward and call the API from outside the cluster"
kubectl --context "$CTX" -n "$NAMESPACE" port-forward "svc/${APP##*/}" "$LOCAL_PORT:80" >/dev/null &
PF_PID=$!
for _ in $(seq 1 30); do
  curl -fsS "http://localhost:$LOCAL_PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://localhost:$LOCAL_PORT/health" | grep -q '"status":"ok"' || fail "/health"
echo "/health ok"
curl -fsS "http://localhost:$LOCAL_PORT/" | grep -q '<div id="root">' || fail "web UI"
echo "web UI ok"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://localhost:$LOCAL_PORT/chat" \
  -H 'Content-Type: application/json' -d '{"session_id": "smoke", "message": "  "}')"
[[ "$code" == "400" ]] || fail "POST /chat with an empty message should be 400, got $code"
echo "/chat validates input"

kubectl --context "$CTX" -n "$NAMESPACE" logs "$APP" | grep -q "DuckDB connected (6 tables available)" \
  || fail "pricing database did not load"
echo "pricing database loaded (6 tables)"

step "All checks passed"
