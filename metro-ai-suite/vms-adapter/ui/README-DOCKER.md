# VMS-UI Docker Setup

## Overview

The VMS-UI frontend is now containerized using Docker with nginx serving the production-optimized React build.

## Quick Start

```bash
# Build the Docker image
cd /home/intel/VMS/VMS-UI
docker build -t vms-ui:latest .

# Run the container
docker run -d \
  --name vms-ui \
  -p 8091:80 \
  --add-host=host.docker.internal:host-gateway \
  vms-ui:latest

# Access the UI
open http://localhost:8091
```

## Docker Configuration

### Dockerfile

Multi-stage build:
- **Stage 1 (builder)**: Node.js 22-alpine, runs `npm ci` and `npm run build`
- **Stage 2 (serve)**: nginx:alpine, serves static files from `/usr/share/nginx/html`

**Image Size**: ~63 MB (highly optimized)

### Nginx Configuration

- **Static Assets**: Served from `/usr/share/nginx/html`
- **API Proxy**: `/v1/*` → `http://host.docker.internal:8085/v1/*`
- **SPA Routing**: All routes redirect to `index.html`
- **Caching**: 1 year for static assets, no-cache for HTML
- **Security Headers**: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection
- **Health Check**: `/health` endpoint for container health monitoring

### Port Configuration

- **Container Port**: 80 (nginx default)
- **Host Port**: 8091 (configurable via `-p` flag)

## Usage

### Using Docker Run

```bash
# Start container
docker run -d \
  --name vms-ui \
  -p 8091:80 \
  --add-host=host.docker.internal:host-gateway \
  --restart unless-stopped \
  vms-ui:latest

# Stop container
docker stop vms-ui

# Remove container
docker rm vms-ui

# View logs
docker logs -f vms-ui

# Check health
curl http://localhost:8091/health
```

### Using Docker Compose (Alternative)

A `docker-compose.yml` is provided but has compatibility issues with docker-compose v1.29.2. Use docker run instead, or upgrade to docker compose v2.

If using docker compose v2:
```bash
# Create .env file
cp .env.example .env

# Edit .env to set UI_PORT (default: 3000, change if port is in use)
# Set BACKEND_HOST (default: host.docker.internal:8085)

# Start
docker compose up -d

# Stop
docker compose down
```

## Connecting to Backend

The frontend container connects to the backend API using `host.docker.internal`, which resolves to the host machine's IP address.

**Requirements**:
- Use `--add-host=host.docker.internal:host-gateway` when running the container
- Backend must be running on the host machine at port 8085

**Alternative**: If both frontend and backend are in the same Docker network:
1. Create a shared network: `docker network create vms-network`
2. Run backend with: `--network vms-network`
3. Run frontend with: `--network vms-network` 
4. Update nginx.conf proxy to use backend container name instead of `host.docker.internal`

## Testing

```bash
# Test health
curl http://localhost:8091/health
# Expected: "healthy"

# Test UI is serving
curl http://localhost:8091/ | grep "VMS Dashboard"
# Expected: Title found

# Test API proxy
curl http://localhost:8091/v1/health
# Expected: {"status":"ok"}

# Test camera API
curl http://localhost:8091/v1/cameras | jq '. | length'
# Expected: Number of discovered cameras
```

## Environment Variables

The nginx configuration hardcodes the backend URL. To make it configurable:

1. Use nginx template with envsubst
2. Modify Dockerfile to run envsubst on startup
3. Set BACKEND_HOST environment variable

Current hardcoded backend: `http://host.docker.internal:8085`

## Troubleshooting

### Port Already in Use

If port 8091 is in use, change the host port:
```bash
docker run -d --name vms-ui -p 8092:80 ... vms-ui:latest
```

### API Proxy Not Working

1. Check backend is running: `curl http://localhost:8085/v1/health`
2. Check host.docker.internal resolves: `docker exec vms-ui ping -c 1 host.docker.internal`
3. Ensure `--add-host=host.docker.internal:host-gateway` is set
4. Check nginx logs: `docker logs vms-ui`

### Container Fails to Start

1. Check logs: `docker logs vms-ui`
2. Verify image built correctly: `docker images vms-ui`
3. Check for port conflicts: `netstat -tulpn | grep 8091`

## Production Deployment

For production:

1. **Build with specific tag**:
   ```bash
   docker build -t vms-ui:v1.0.0 .
   ```

2. **Use docker compose with proper networking**:
   - Create a shared network for frontend + backend
   - Use docker compose v2 or higher
   - Set restart policies and resource limits

3. **Configure reverse proxy** (optional):
   - Use nginx or Traefik in front of both services
   - Handle SSL/TLS termination
   - Centralize logging and monitoring

4. **Health checks**:
   - Container has built-in healthcheck
   - Monitor `/health` endpoint externally
   - Set up alerts for container failures

## Files Created

- `Dockerfile` - Multi-stage build configuration
- `nginx.conf` - Nginx server configuration  
- `.dockerignore` - Build context optimization
- `docker-compose.yml` - Docker Compose service definition
- `.env.example` - Environment variable template
- `README-DOCKER.md` - This documentation

## Next Steps

- Integrate frontend and backend in a single `docker-compose.yml` at repository root
- Add environment variable support for configurable backend URL
- Set up CI/CD pipeline for automated image builds
- Push images to container registry (Docker Hub, GitHub Container Registry, etc.)
- Configure SSL/TLS for HTTPS
