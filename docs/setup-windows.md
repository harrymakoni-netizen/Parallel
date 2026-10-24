# Setting up Parallel on a Windows computer, from scratch

This guide takes you from a clean Windows computer to a working copy of Parallel that you can sign in
to, with a test project run on a real engine. It assumes no technical background beyond copying and
pasting commands. Allow about an hour, most of it waiting for downloads.

Parallel runs as a set of containers (database, queue, API, workers, web application) managed by
Docker. Everything stays on your computer unless you choose to share it (Part 7).

## Contents

1. What you need
2. Install the tools
3. Get the code
4. Configure
5. Build and start
6. Create accounts and sign in
7. Share it with other people (optional)
8. Day-to-day use
9. Updating, backing up and removing
10. Troubleshooting

---

## 1. What you need

| Requirement | Detail |
| --- | --- |
| Windows | Windows 10 (22H2) or Windows 11, 64-bit |
| Memory | 16 GB recommended; 8 GB is the minimum, and you must give Docker most of it |
| Processor | 4 cores or more, with virtualisation switched on in the BIOS (almost always the default) |
| Disk | About 30 GB free (the images that contain the engines are large) |
| Internet | The first build downloads several gigabytes |
| Access | Permission to read the code repository (see step 3), and an administrator password for the install steps |

You also need to choose two secrets, both generated for you in step 4: a **secret key** that signs
sign-in sessions, and a **database password**. You never type either of them again.

Anything you see written like `this` is a command to paste into **Windows PowerShell**, then press
Enter. Where a command contains a value in capitals such as `YOUR-EMAIL`, replace it with your own.

## 2. Install the tools

You do this once.

### 2.1 Windows Subsystem for Linux

Docker needs it. Open the Start menu, type **PowerShell**, right-click **Windows PowerShell**, and
choose **Run as administrator**. Then:

```powershell
wsl --install
```

Restart the computer when it asks. If it reports that virtualisation is disabled, restart into the BIOS
or UEFI settings and enable the option called *Virtualization*, *Intel VT-x*, *AMD-V* or *SVM*.

### 2.2 Docker Desktop

1. Download **Docker Desktop for Windows** from docker.com and install it, keeping **Use WSL 2** ticked.
2. Start Docker Desktop and accept the terms. Wait until the whale icon in the system tray stops
   animating and the window says Docker is running.
3. Open **Settings → Resources**. Give Docker at least **4 CPUs** and **8 GB of memory** (more if you
   have it), then **Apply & restart**.

### 2.3 Git and the tunnel tool

Open a **normal** (not administrator) PowerShell window and run:

```powershell
winget install Git.Git
winget install Cloudflare.cloudflared
```

`cloudflared` is only needed for Part 7. **Close PowerShell and open a new one** so the new commands are
recognised. Check:

```powershell
git --version
docker --version
```

Both should print a version number.

## 3. Get the code

Always work from your own user folder. A PowerShell window opened as administrator starts in
`C:\WINDOWS\system32`, where you cannot create folders; if your prompt shows that path, run `cd $HOME`
first.

```powershell
cd $HOME
git config --global core.autocrlf false
git clone -b claude/gracious-allen-6xoln9 https://github.com/harrymakoni-netizen/Parallel.git
cd Parallel
```

- The `core.autocrlf false` line matters. Without it, Git for Windows converts some files to Windows
  line endings, and the build then fails with `build-ca: not found`.
- If the repository is private, Git opens a browser window asking you to sign in to GitHub. The account
  you use must have been given access by the owner.
- The prompt should now read `PS C:\Users\<you>\Parallel>`. Every later command in this guide is run from
  this folder.

## 4. Configure

Parallel reads its settings from a file called `.env`. Create it from the example, then fill in the
important lines with the script below (no text editor needed).

```powershell
copy .env.example .env
```

Now paste this whole block. It generates a random secret key and database password and writes them into
`.env`. The address on the first line is where you will open the application; leave it as it is unless
you are following Part 7.

```powershell
$url = "http://localhost:8080"
$k = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
$p = [guid]::NewGuid().ToString("N")
$f = "$PWD\.env"
$t = [IO.File]::ReadAllText($f)
$t = $t -replace '(?m)^PARALLEL_ENVIRONMENT=.*$', 'PARALLEL_ENVIRONMENT=production'
$t = $t -replace '(?m)^PARALLEL_SECRET_KEY=.*$', "PARALLEL_SECRET_KEY=$k"
$t = $t -replace '(?m)^POSTGRES_PASSWORD=.*$', "POSTGRES_PASSWORD=$p"
$t = $t -replace '(?m)^PARALLEL_PUBLIC_BASE_URL=.*$', "PARALLEL_PUBLIC_BASE_URL=$url"
$t = $t -replace '(?m)^PARALLEL_WEB_ORIGIN=.*$', "PARALLEL_WEB_ORIGIN=$url"
[IO.File]::WriteAllText($f, $t, (New-Object Text.UTF8Encoding($false)))
Select-String -Path .env -Pattern '^(PARALLEL_ENVIRONMENT|PARALLEL_PUBLIC_BASE_URL|PARALLEL_WEB_ORIGIN)='
```

The last command should print three lines: `production` and your address twice. If it does, the file is
right. **Keep `.env` private** — it holds the secrets. It is excluded from Git, so it is never uploaded.
Do not run the block a second time later: it would generate a new database password that no longer
matches the existing database. To change only the address, see Part 7.

## 5. Build and start

Make sure Docker Desktop is running, then:

```powershell
docker compose up -d --build
```

The first run downloads and builds everything and commonly takes **20 to 40 minutes**. Progress scrolls
by; it is normal for it to look stuck for several minutes. When the prompt returns, check the result:

```powershell
docker compose ps
```

You should see these services, each `running` or `healthy` (the `migrate` job shows `exited` — that is
correct, it only runs once to prepare the database):

| Service | What it does |
| --- | --- |
| `db` | PostgreSQL with the PostGIS extension |
| `redis` | The job queue |
| `api` | The HTTP API |
| `sim` | Runs the simulation engines |
| `recon` | Processes captured photographs and video |
| `results` | Builds PDF, CSV and JSON reports |
| `scheduler` | Scheduler for recovery and engine checks |
| `web` | The browser application |

If the build fails with a `ReadTimeoutError` or another download error, it is a temporary network
problem. Run the same command again; finished steps are kept, so each attempt gets further. See the
troubleshooting table for other errors.

## 6. Create accounts and sign in

### 6.1 Your administrator account

Choose a password of **at least 12 characters** and replace `YOUR-PASSWORD` and `YOUR-EMAIL`. Putting
the password in the command is deliberate: the password prompt that appears otherwise shows no
characters while you type, which makes it easy to mistype.

```powershell
docker compose run --rm -e PARALLEL_BOOTSTRAP_PASSWORD="YOUR-PASSWORD" api python -m parallel_api.admin create-admin --email YOUR-EMAIL --organisation "Your organisation"
```

For a second person, run it again with their email, their password and a different organisation name.
(Your password is stored only as a hash in the database; you can change it later with
`python -m parallel_api.admin set-password --email YOUR-EMAIL`.)

### 6.2 Optional: the demonstration projects

These are five prepared example projects, one for each module, built by running the real engines once.
They are clearly labelled as synthetic. Choose a password of 12 or more characters:

```powershell
docker compose run --rm -e PARALLEL_DEMO_PASSWORD="DEMO-PASSWORD" sim python -m parallel_api.demo seed
```

It takes several minutes. It creates two accounts, `presenter@parallel-demo.example` (administrator) and
`reviewer@parallel-demo.example`, both with that password. See [demo-guide.md](demo-guide.md).

### 6.3 Sign in

Open **http://localhost:8080** in your browser and sign in with the account from 6.1. Then check:

- **Engines** page: lists every engine with its real state. An engine that is not installed or licensed
  is shown as unavailable, with a next step; that is by design, never a failure to fix. The engines
  included in the worker image (JuPedSim, SUMO, SWMM, EPANET, SimPy, OpenFOAM, OpenStudio) should show
  as ready; licensed engines need their own licences.
- **Demonstrations** page (if you ran 6.2): five finished example projects.

### 6.4 A first test project

1. **Projects → new project**.
2. **Name:** `Test drainage`. **Project type:** Urban Drainage. **Start from:** the Urban Drainage (SWMM)
   template. Leave the location fields blank. Click **Create project**.
3. Inside the project the tabs are Overview, Captures, Scene Workspace, Models, Scenarios, Runs, Compare,
   Reports and Activity. Open **Models** to see the starter model, **Scenarios** to see the starter
   scenario, then start a run and watch it under **Runs**.
4. When it finishes, open **Reports** to generate a report from it.

Templates add clearly labelled synthetic models and scenarios. They never add results: every result you
see comes from a real engine run.

## 7. Share it with other people (optional)

By default the application is reachable only from your own computer. To let someone else use it over the
internet without opening your router, use a Cloudflare quick tunnel. Be aware of what this means:

- Anyone with the web address can reach your sign-in page, so use strong passwords.
- Your computer must stay on, with Docker running and the tunnel open, whenever others use it.
- The address changes every time the tunnel is restarted.

**7.1** Start the tunnel in a **second** PowerShell window and leave it open:

```powershell
cloudflared tunnel --url http://localhost:8080
```

It prints an address like `https://random-words.trycloudflare.com`. Copy it.

**7.2** In your first window, in the `Parallel` folder, tell the application its public address
(replace the address with the one you copied) and restart the services that use it:

```powershell
$url = "https://random-words.trycloudflare.com"
$f = "$PWD\.env"
$t = [IO.File]::ReadAllText($f)
$t = $t -replace '(?m)^PARALLEL_PUBLIC_BASE_URL=.*$', "PARALLEL_PUBLIC_BASE_URL=$url"
$t = $t -replace '(?m)^PARALLEL_WEB_ORIGIN=.*$', "PARALLEL_WEB_ORIGIN=$url"
[IO.File]::WriteAllText($f, $t, (New-Object Text.UTF8Encoding($false)))
docker compose up -d
```

**7.3** Send the address to the people who need it. They sign in with the accounts you created in 6.1.
Each time you restart the tunnel, repeat 7.1 and 7.2 with the new address.

For a permanent address, create a free Cloudflare account and a named tunnel on a domain you own
(Cloudflare's *Zero Trust* documentation covers it), or host Parallel on a cloud machine with a public
name instead; see [google-cloud.md](google-cloud.md) and [deployment.md](deployment.md).
## 8. Day-to-day use

All commands are run from the `Parallel` folder.

| Task | Command |
| --- | --- |
| Start after a restart | open Docker Desktop, wait for it to run; the services start by themselves. Check with `docker compose ps`, or run `docker compose up -d` |
| Stop (keeps all data) | `docker compose stop` |
| See what is running | `docker compose ps` |
| Follow the logs | `docker compose logs -f api` (or `sim`, `recon`, `results`, `web`) |
| Open the application | http://localhost:8080 |
| Start the tunnel again | `cloudflared tunnel --url http://localhost:8080`, then Part 7.2 |

Your projects, results and files live in Docker volumes and survive restarts and updates.

## 9. Updating, backing up and removing

**Update to the latest version**

```powershell
git pull
docker compose up -d --build
```

The database is upgraded automatically. If `git pull` complains about line endings or local changes in a
file you did not edit, run `git config core.autocrlf false`, then `git rm --cached -r -q .` and
`git reset --hard` (your `.env` is not tracked and is kept).

**Back up** (database and every stored file, with a verification of the copy):

```powershell
mkdir backups
docker compose run --rm --no-deps -v "${PWD}/backups:/backups" sim python -m parallel_core.backup create --out /backups/backup-1
docker compose run --rm --no-deps -v "${PWD}/backups:/backups" sim python -m parallel_core.backup verify --from /backups/backup-1
```

Copy the `backups` folder somewhere safe, away from this computer. Restoring is described in
[deployment.md](deployment.md#backups). If the backup command reports a permission error writing to
the folder, make the folder writable by everyone first (`icacls backups /grant Everyone:F`).

**Remove everything**

```powershell
docker compose down        # stops and removes the containers; the data is kept
docker compose down -v     # also DELETES the database and stored files — this cannot be undone
```

## 10. Troubleshooting

| What you see | Cause and fix |
| --- | --- |
| `fatal: could not create work tree dir 'Parallel': Permission denied` | PowerShell is in `C:\WINDOWS\system32`. Run `cd $HOME` and clone again. |
| `git` or `docker` or `cloudflared` is not recognised | Close PowerShell and open a new window after installing. |
| `error during connect` / `cannot connect to the Docker daemon` | Docker Desktop is not running. Open it and wait for it to say it is running. |
| Build stops with `build-ca: not found` (exit code 127) | Windows line endings in the scripts. Run `git config core.autocrlf false`, `git rm --cached -r -q .`, `git reset --hard`, then build again. |
| Build stops with `ReadTimeoutError`, `TLS handshake timeout` or another download error | Temporary network problem. Run `docker compose up -d --build` again; turn off a VPN or proxy if it keeps happening. |
| `no space left on device` | Free disk space, or in Docker Desktop use *Troubleshoot → Clean / Purge data* (this deletes stored projects) or `docker builder prune`. |
| Build is killed or very slow | Docker has too little memory. Raise it in Settings → Resources (8 GB minimum). |
| `set PARALLEL_SECRET_KEY in .env` or `set POSTGRES_PASSWORD in .env` | The `.env` file is missing or the Part 4 block was not run. Do Part 4 again from the beginning (only before first start; see the warning there). |
| Password prompt shows nothing as you type | Normal: it hides input. Use the `PARALLEL_BOOTSTRAP_PASSWORD` form in 6.1. |
| `Passwords must have at least 12 characters` | Use a longer password. |
| Sign-in fails after you changed `.env` | The services still have the old settings. Run `docker compose up -d`. |
| Sign-in page loads but sign-in fails over the tunnel | The two addresses in `.env` do not match the tunnel address, or the services were not restarted. Repeat Part 7.2. |
| `password authentication failed for user "parallel"` | The `.env` database password changed after the database was first created. Put the original value back, or, if there is no data worth keeping, `docker compose down -v` and start again. |
| An engine shows *Not installed* or *Worker unavailable* | The state is real. Read the next step shown on the Engines page. Licensed engines (Ventsim, FDS, FLAC3D, 3DEC, Virtual CRASH, PC-Crash, HEC-RAS) need your own licence; Isaac Sim and splat training need a GPU worker ([deployment.md](deployment.md#gpu-worker-isaac-sim-gaussian-splat-training)). |
| Port 8080 is already in use | Another program has it. Add `PARALLEL_HTTP_PORT=8081` to `.env`, change the address in `.env` to match, and run `docker compose up -d`. |

If something else goes wrong, collect the output of `docker compose ps` and `docker compose logs --tail 100 api`
and send them with a description of the step you were on.
