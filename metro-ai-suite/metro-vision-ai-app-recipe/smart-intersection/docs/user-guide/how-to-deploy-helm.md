# Deploy with Helm

Use Helm to deploy Smart Intersection to a Kubernetes cluster. This guide will help you:
- Add the Helm chart repository.
- Configure the Helm chart to match your deployment needs.
- Deploy and verify the application.

Helm simplifies Kubernetes deployments by streamlining configurations and enabling easy scaling and updates. For more details, see [Helm Documentation](https://helm.sh/docs/).


## Prerequisites

Before You Begin, ensure the following:

- **Kubernetes Cluster**: Ensure you have a properly installed and configured Kubernetes cluster.
- **System Requirements**: Verify that your system meets the [minimum requirements](./system-requirements.md).
- **Tools Installed**: Install the required tools:
    - Kubernetes CLI (kubectl)
    - Helm 3 or later

## Steps to Deploy

To deploy the Smart Intersection Sample Application, copy and paste the entire block of commands below into your terminal and run them:

### Clone the Repository and Install Prerequisites

**Note**: Skip this step if you have already followed the steps as part of the [Get Started guide](./get-started.md).

Before you can deploy with Helm, you need to clone the repository and run the installation script:

```bash
# Clone the repository
git clone https://github.com/open-edge-platform/edge-ai-suites.git

# Navigate to the Metro AI Suite directory
cd edge-ai-suites/metro-ai-suite/metro-vision-ai-app-recipe/

# Run the installation script for smart-intersection
./install.sh smart-intersection

```

### Configure Proxy Settings (If Behind a Proxy)

If you are deploying in a proxy environment, update the values.yml file with your proxy settings before installation:

```bash
# Edit the values.yml file to add proxy configuration
nano ./smart-intersection/chart/values.yaml
```

Add the following proxy configuration to your values.yml:

```yaml
# Proxy configuration
proxy:
  http_proxy: "http://your-proxy-server:port"
  https_proxy: "http://your-proxy-server:port"
  no_proxy: "localhost,127.0.0.1,.local,.cluster.local"
```

Replace `your-proxy-server:port` with your actual proxy server details.

### Deploy the application

Now you're ready to deploy the Smart Intersection application:

```bash

# Install the chart 
helm upgrade --install smart-intersection ./smart-intersection/chart \
  --create-namespace \
  --set grafana.service.type=NodePort \
  -n smart-intersection

```

## Access Application Services

### Access the Application UI using Node Port

- Get the Node Port Number using following command and use it to access the Application UI
```bash
kubectl get service smart-intersection-web -n smart-intersection -o jsonpath='{.spec.ports[0].nodePort}'
```
- Go to http://<HOST_IP>:<Node_PORT>

### Access the Grafana UI using Node Port

- Get the Node Port Number using following command and use it to access the Grafana UI
```bash
kubectl get service smart-intersection-grafana -n smart-intersection -o jsonpath='{.spec.ports[0].nodePort}'
```
- Go to http://<HOST_IP>:<Node_PORT>

### Access the InfluxDB UI using Port Forwarding

```bash
INFLUX_POD=$(kubectl get pods -n smart-intersection -l app=smart-intersection-influxdb -o jsonpath="{.items[0].metadata.name}")
kubectl -n smart-intersection port-forward $INFLUX_POD 8086:8086
```

### Access the NodeRED UI using Port Forwarding

```bash
NODE_RED_POD=$(kubectl get pods -n smart-intersection -l app=smart-intersection-nodered -o jsonpath="{.items[0].metadata.name}")
kubectl -n smart-intersection port-forward $NODE_RED_POD 1880:1880
```

### Access the DL Streamer Pipeline Server using Port Forwarding

```bash
DLS_PS_POD=$(kubectl get pods -n smart-intersection -l app=smart-intersection-dlstreamer-pipeline-server -o jsonpath="{.items[0].metadata.name}")
kubectl -n smart-intersection port-forward $DLS_PS_POD 8080:8080
kubectl -n smart-intersection port-forward $DLS_PS_POD 8555:8555
```

## How to Uninstall the Application

To uninstall the application, run the following command:

```bash
helm uninstall smart-intersection -n smart-intersection
```

## How to Delete the Namespace

To delete the namespace and all resources within it, run the following command:

```bash
kubectl delete namespace smart-intersection
```

## What to Do Next

- **[How to Use the Application](./how-to-use-application.md)**: Verify the application and access its features.
- **[Troubleshooting Helm Deployments](./support.md#troubleshooting-helm-deployments)**: Consolidated troubleshooting steps for resolving issues during Helm deployments.
- **[Get Started](./get-started.md)**: Ensure you have completed the initial setup steps before proceeding.

## Supporting Resources

- [Kubernetes Documentation](https://kubernetes.io/docs/home/)
- [Helm Documentation](https://helm.sh/docs/)
