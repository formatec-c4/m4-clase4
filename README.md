# Módulo 4 · Clase 4 — Seguridad en capas

Guía práctica del alumno para Docker Desktop con Kubernetes habilitado. El
laboratorio utiliza herramientas open source y no reemplaza el cluster ni su
CNI.

## Recorrido de la clase

| Capa | Herramienta | Prueba |
|---|---|---|
| Imagen | Docker y Trivy | Construir, endurecer y escanear. |
| Integridad | Cosign | Firmar y verificar una imagen por digest. |
| Admisión | Kyverno | Bloquear un Deployment y mutar otro. |
| Runtime | Tetragon | Observar y bloquear tráfico este-oeste. |
| Visualización | Headlamp | Consultar recursos, eventos y logs. |

Duración estimada: 90 minutos.

## Requisitos

- Docker Desktop con Kubernetes en estado `Running`.
- `kubectl`, Helm, `jq`, `curl` y Git.
- Acceso a internet.
- En Windows, utilizar Git Bash o WSL para ejecutar los bloques.

```bash
docker version
kubectl config use-context docker-desktop
kubectl get nodes
helm version
jq --version
```

El nodo debe aparecer como `Ready`. El contexto se selecciona una sola vez;
desde ese momento `kubectl` y Helm trabajan sobre `docker-desktop`.

## 1. Construir las imágenes

```bash
cd supply-chain

docker build \
  -f Dockerfile.vulnerable \
  -t formatec/security-lab:vulnerable \
  .

docker build \
  -f Dockerfile.secure \
  -t formatec/security-lab:safe \
  .

docker image ls formatec/security-lab
```

Comprobar que la aplicación responde:

```bash
docker run --rm -d \
  --name formatec-safe \
  -p 18080:8080 \
  formatec/security-lab:safe

curl --fail http://localhost:18080/health
docker stop formatec-safe
```

Comparar el usuario configurado:

```bash
docker image inspect formatec/security-lab:vulnerable \
  --format 'Vulnerable: {{json .Config.User}}'

docker image inspect formatec/security-lab:safe \
  --format 'Segura: {{json .Config.User}}'
```

Una cadena vacía indica que la imagen no definió un usuario y se ejecutará como
root por defecto.

## 2. Escanear con Trivy

```bash
mkdir -p .trivy-cache

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/.trivy-cache:/root/.cache" \
  aquasec/trivy:0.74.0 image \
  --severity HIGH,CRITICAL \
  --ignore-unfixed \
  --no-progress \
  formatec/security-lab:vulnerable
```

Convertir el análisis en un gate que devuelve código `1` si encuentra
vulnerabilidades:

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/.trivy-cache:/root/.cache" \
  aquasec/trivy:0.74.0 image \
  --severity HIGH,CRITICAL \
  --ignore-unfixed \
  --exit-code 1 \
  --no-progress \
  formatec/security-lab:vulnerable

echo $?
```

Repetir sobre la imagen endurecida:

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/.trivy-cache:/root/.cache" \
  aquasec/trivy:0.74.0 image \
  --severity HIGH,CRITICAL \
  --ignore-unfixed \
  --exit-code 1 \
  --no-progress \
  formatec/security-lab:safe

echo $?
```

Trivy encuentra vulnerabilidades conocidas en los componentes. No reemplaza
las pruebas de la aplicación ni la observación en runtime.

## 3. Firmar con Cosign

Crear un registry local para publicar la imagen aprobada:

```bash
docker network create formatec-cosign

docker run -d \
  --name formatec-registry \
  --network formatec-cosign \
  -p 5050:5000 \
  registry:3.1.1

curl --fail http://localhost:5050/v2/
```

Publicar y obtener el digest inmutable:

```bash
docker tag \
  formatec/security-lab:safe \
  localhost:5050/formatec/security-lab:safe

docker push localhost:5050/formatec/security-lab:safe

DIGEST="$(
  docker image inspect localhost:5050/formatec/security-lab:safe \
    --format '{{join .RepoDigests "\n"}}' | \
    awk -F@ '/^localhost:5050\/formatec\/security-lab@sha256:/ {print $2; exit}'
)"

COSIGN_IMAGE="formatec-registry:5000/formatec/security-lab@${DIGEST}"
echo "$COSIGN_IMAGE"
```

Generar claves descartables para el laboratorio:

```bash
export COSIGN_PASSWORD='formatec-lab'
mkdir -p keys

docker run --rm \
  -e COSIGN_PASSWORD \
  -v "$PWD/keys:/keys" \
  -w /keys \
  gcr.io/projectsigstore/cosign:v3.1.2 \
  generate-key-pair
```

Firmar y verificar:

```bash
docker run --rm \
  --network formatec-cosign \
  -e COSIGN_PASSWORD \
  -v "$PWD/keys:/keys:ro" \
  gcr.io/projectsigstore/cosign:v3.1.2 \
  sign --yes \
  --allow-http-registry \
  --use-signing-config=false \
  --tlog-upload=false \
  --key /keys/cosign.key \
  "$COSIGN_IMAGE"

docker run --rm \
  --network formatec-cosign \
  -v "$PWD/keys:/keys:ro" \
  gcr.io/projectsigstore/cosign:v3.1.2 \
  verify \
  --allow-http-registry \
  --insecure-ignore-tlog=true \
  --key /keys/cosign.pub \
  "$COSIGN_IMAGE"
```

La verificación demuestra que la firma corresponde al mismo digest publicado.
La advertencia sobre el transparency log es esperable porque esta práctica es
completamente local.

## 4. Instalar Tetragon, Kyverno y Headlamp

Volver a la raíz:

```bash
cd ..
```

Agregar los repositorios Helm:

```bash
helm repo add cilium https://helm.cilium.io --force-update
helm repo add kyverno https://kyverno.github.io/kyverno/ --force-update
helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/ --force-update
helm repo update
```

Instalar las herramientas:

```bash
helm upgrade --install tetragon cilium/tetragon \
  --version 1.7.1 \
  --namespace kube-system \
  --wait --timeout 5m

helm upgrade --install kyverno kyverno/kyverno \
  --version 3.9.0 \
  --namespace kyverno \
  --create-namespace \
  --set global.image.registry=ghcr.io \
  --wait --timeout 5m

helm upgrade --install headlamp headlamp/headlamp \
  --version 0.45.0 \
  --namespace headlamp \
  --create-namespace \
  --wait --timeout 5m
```

Crear el namespace y aplicar los recursos del laboratorio:

```bash
kubectl create namespace security-lab \
  --dry-run=client -o yaml | \
  kubectl apply -f -

kubectl apply \
  -f k8s/manifests/headlamp-viewer-rbac.yaml

kubectl apply \
  -f k8s/manifests/kyverno-disallow-privileged.yaml

kubectl apply \
  -f k8s/manifests/kyverno-add-seccomp.yaml

kubectl apply \
  -f k8s/manifests/tetragon-observe-tcp.yaml

kubectl apply \
  -f k8s/manifests/demo-east-west.yaml
```

Comprobar el estado:

```bash
kubectl get pods -n kyverno
kubectl get pods -n kube-system \
  -l app.kubernetes.io/name=tetragon
kubectl get pods -n headlamp
kubectl get pods,service -n security-lab
kubectl get validatingpolicy,mutatingpolicy
```

## 5. Kyverno: bloquear un Deployment

```bash
kubectl apply \
  -f k8s/manifests/demo-privileged-denied.yaml
```

Kyverno debe rechazar el recurso con este mensaje:

```text
Los Deployments privilegiados estan bloqueados por Kyverno.
```

```bash
kubectl get deployment privileged-demo \
  -n security-lab
```

El resultado debe ser `NotFound`.

## 6. Kyverno: mutar un Deployment

Crear un Deployment sin seccomp declarado:

```bash
kubectl delete deployment mutated-demo \
  -n security-lab --ignore-not-found

kubectl apply \
  -f k8s/manifests/demo-mutated-deployment.yaml
```

Comprobar la mutación realizada durante admission:

```bash
kubectl get deployment mutated-demo \
  -n security-lab \
  -o jsonpath='label={.spec.template.metadata.labels.security\.formatec\.io/mutated}{" seccomp="}{.spec.template.spec.securityContext.seccompProfile.type}{"\n"}'
```

Salida esperada:

```text
label=kyverno seccomp=RuntimeDefault
```

La validación rechaza configuraciones prohibidas. La mutación completa una
configuración permitida antes de almacenarla.

## 7. Tetragon: observar tráfico este-oeste

En la primera terminal, seguir los eventos TCP del namespace:

```bash
kubectl logs -n kube-system \
  daemonset/tetragon -c export-stdout --since=1s -f | \
  jq --unbuffered -c '
    select(
      .process_kprobe.policy_name == "observe-tcp-connect" and
      .process_kprobe.process.pod.namespace == "security-lab"
    ) |
    {
      origen: .process_kprobe.process.pod.name,
      proceso: .process_kprobe.process.binary,
      ip_destino: .process_kprobe.args[0].sock_arg.daddr,
      puerto: .process_kprobe.args[0].sock_arg.dport
    }'
```

En otra terminal, generar tráfico hacia el Service y hacia el Pod backend:

```bash
kubectl exec -n security-lab frontend -- \
  wget -qO- http://backend

BACKEND_IP="$(kubectl get pod \
  -n security-lab -l app=backend \
  -o jsonpath='{.items[0].status.podIP}')"

kubectl exec -n security-lab frontend -- \
  wget -qO- "http://${BACKEND_IP}:8080"
```

Tetragon muestra el proceso, la IP de destino y el puerto. La primera conexión
usa el `ClusterIP` del Service y la segunda utiliza el `PodIP`.

Hubble no se utiliza porque depende de Cilium como CNI. Tetragon puede observar
este runtime sin reemplazar Kindnet en el cluster existente de Docker Desktop.

### Bloquear una conexión con Tetragon

Aplicar una política de enforcement que bloquea conexiones al puerto `8080`
dentro de `security-lab`:

```bash
kubectl apply \
  -f k8s/manifests/tetragon-block-direct-backend.yaml
```

La conexión mediante el Service sigue permitida porque usa el puerto `80`:

```bash
kubectl exec -n security-lab frontend -- \
  wget -qO- http://backend
```

La conexión directa al Pod usa el puerto `8080` y queda bloqueada:

```bash
BACKEND_IP="$(kubectl get pod \
  -n security-lab -l app=backend \
  -o jsonpath='{.items[0].status.podIP}')"

kubectl exec -n security-lab frontend -- \
  wget -T 3 -qO- "http://${BACKEND_IP}:8080"
```

Resultado esperado:

```text
Operation not permitted
```

La acción `Override` intercepta `security_socket_connect` y devuelve un error
al proceso antes de establecer la conexión. Eliminar la política restaura el
acceso:

```bash
kubectl delete tracingpolicynamespaced block-direct-backend \
  -n security-lab

kubectl exec -n security-lab frontend -- \
  wget -qO- "http://${BACKEND_IP}:8080"
```

Este ejemplo demuestra enforcement de runtime. No reemplaza una
`NetworkPolicy`: Tetragon trabaja con eventos y acciones del kernel, mientras
que la política de red expresa qué comunicaciones deberían estar permitidas.

## 8. Headlamp: consultar el cluster

Generar un token de solo lectura:

```bash
kubectl create token \
  headlamp-viewer \
  -n headlamp \
  --duration=8h
```

En otra terminal, abrir el acceso local:

```bash
kubectl port-forward \
  -n headlamp service/headlamp 8080:80
```

Abrir <http://localhost:8080>, ingresar el token y revisar:

- Pods, Services y Events de `security-lab`;
- las políticas de Kyverno;
- los logs `export-stdout` del Pod de Tetragon.

Headlamp permite explorar el estado actual. No reemplaza la retención de logs,
los audit logs del API ni un SIEM.

## 9. Limpieza

Eliminar las aplicaciones y políticas del laboratorio:

```bash
kubectl delete namespace security-lab
kubectl delete validatingpolicy \
  disallow-privileged-deployments
kubectl delete mutatingpolicy \
  add-seccomp-to-demo
kubectl delete clusterrolebinding \
  headlamp-viewer headlamp-security-observer
kubectl delete clusterrole \
  headlamp-security-observer
```

Opcionalmente, desinstalar las herramientas:

```bash
helm uninstall headlamp --namespace headlamp
helm uninstall kyverno --namespace kyverno
helm uninstall tetragon --namespace kube-system
kubectl delete namespace \
  headlamp kyverno --ignore-not-found
```

Eliminar los artefactos locales del ejercicio:

```bash
docker container rm -f formatec-registry 2>/dev/null || true
docker network rm formatec-cosign 2>/dev/null || true
docker image rm \
  formatec/security-lab:vulnerable \
  formatec/security-lab:safe \
  localhost:5050/formatec/security-lab:safe \
  registry:3.1.1 \
  gcr.io/projectsigstore/cosign:v3.1.2 \
  aquasec/trivy:0.74.0 2>/dev/null || true
```

Los comandos de limpieza usan nombres explícitos y no eliminan ni reinician el
cluster Docker Desktop.

## Archivos

```text
supply-chain/     aplicación y Dockerfiles
k8s/manifests/    aplicaciones y políticas del laboratorio
```

## Referencias

- Docker: <https://docs.docker.com/build/building/best-practices/>
- Trivy: <https://trivy.dev/docs/latest/>
- Cosign: <https://docs.sigstore.dev/cosign/signing/signing_with_containers/>
- Kyverno: <https://kyverno.io/docs/policy-types/>
- Tetragon: <https://tetragon.io/docs/>
- Headlamp: <https://headlamp.dev/docs/latest/>
