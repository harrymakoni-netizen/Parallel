# Running Parallel on one Google Cloud virtual machine

Suitable for a small team (a handful of simultaneous users). The same Docker Compose stack as
[deployment.md](deployment.md), on one Compute Engine instance, with HTTPS from Caddy.
Prices and the free-credit offer change; check them on the Google Cloud pages before you commit.

## 1. Account and project

Sign up at cloud.google.com, enable billing (a card is required), and create a project.

## 2. Reserve a static address

Console → VPC network → IP addresses → Reserve external static IP address. Name it `parallel-ip`,
pick a region near your users (for example `europe-west2`), and note the address.

## 3. Create the instance

Console → Compute Engine → VM instances → Create instance:

| Setting | Value |
| --- | --- |
| Machine type | `e2-standard-4` (4 vCPU, 16 GB) |
| Boot disk | Ubuntu 24.04 LTS, balanced persistent disk, 100 GB |
| Firewall | Allow HTTP traffic and Allow HTTPS traffic |
| Networking → external IPv4 | the static address from step 2 |

Stop the instance when nobody is using it; a stopped instance bills only for its disk.

## 4. A name for the address

HTTPS certificates need a host name. Either buy a domain and add an `A` record pointing at the static
address, or use a free DuckDNS name (duckdns.org: sign in, create `yourname`, set its address to the
static IP). Wait a few minutes for DNS, then `nslookup yourname.duckdns.org` should return the address.

## 5. Install Docker and fetch the code

Open SSH from the instance list, then:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
git clone -b claude/gracious-allen-6xoln9 https://github.com/harrymakoni-netizen/Parallel.git
cd Parallel
cp .env.example .env
```

## 6. Configure

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # copy for PARALLEL_SECRET_KEY
nano .env
```

Set, with your own values:

```
PARALLEL_ENVIRONMENT=production
PARALLEL_SECRET_KEY=<the generated value>
POSTGRES_PASSWORD=<a long password>
PARALLEL_DOMAIN=yourname.duckdns.org
PARALLEL_PUBLIC_BASE_URL=https://yourname.duckdns.org
PARALLEL_WEB_ORIGIN=https://yourname.duckdns.org
```

## 7. Start

```bash
docker compose -f compose.yaml -f compose.https.yaml up -d --build
docker compose -f compose.yaml -f compose.https.yaml ps
```

The first build downloads the engines and takes a while. All services should show `running` or
`healthy`; Caddy fetches the certificate on first request.

## 8. Accounts

```bash
alias dc='docker compose -f compose.yaml -f compose.https.yaml'
dc run --rm api python -m parallel_api.admin create-admin --email you@example.org --organisation "Your organisation"
```

Run it again with your partner's email and the same organisation name, or invite them from the
application's members page. Optional demonstration projects (run the real engines once):

```bash
dc run --rm -e PARALLEL_DEMO_PASSWORD=<choose one> sim python -m parallel_api.demo seed
```

## 9. Check

Open `https://yourname.duckdns.org` and sign in. Settings → Engines shows each engine's real state.

## Operations

- Back up: `dc run --rm api python -m parallel_core.backup create` (see deployment.md → Backups) and
  copy the result off the machine, or take Compute Engine disk snapshots on a schedule.
- Update: `git pull`, then step 7 again; migrations run as part of the stack.
- Logs: `dc logs -f api` (or `sim`, `recon`, `results`, `caddy`).
- Costs: stop the instance when unused; delete the static address if you remove the instance, as an
  unused reserved address is billed.

## Using the Vercel site as well (optional)

With HTTPS in place the application is already served from the instance. To keep the Vercel
deployment, replace the first rewrite in `apps/web/vercel.json` with
`{ "source": "/api/(.*)", "destination": "https://yourname.duckdns.org/api/$1" }`, push, and set the
two URL settings above to the Vercel address instead. Large uploads are better made to the instance
directly.
