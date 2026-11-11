# Running locally or on a VPS

The reference environment is Ubuntu Linux, PostgreSQL 18, a CPU-only llama.cpp build, and a Conda environment with Python 3.11. The reference VPS has six Intel Xeon Platinum 8272CL vCPUs and 16 GB RAM. This is a reference configuration, not a minimum. Keep model, backend and database listeners on loopback. A local Linux machine follows the same instructions; other platforms are unvalidated.

## Environment and model

From the repository root:

```bash
conda env create -f environment.yml
conda activate llama
```

`environment.yml` is the dependency source of truth. Install Git, CMake, Make and a C++ compiler if unavailable. Before obtaining weights, personally review and accept the access terms on the [official model page](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) and review the [Llama 3.2 Community License](../legal/LLAMA-3.2-LICENSE.txt). The flag below records your attestation; it cannot accept terms for you.

```bash
python -m inference.setup --jobs 3 --accept-llama-license
python -m inference.setup --verify-only
```

Setup builds the pinned CPU runtime and checks the exact Q4_K_M artifact's size, SHA-256, metadata and chat template against [the lock](../inference/artifacts.lock.json). Runtime, model and private data remain in ignored `.runtime/`. Repeat verification and real inference tests after any model-byte or pin change.

## PostgreSQL

Inspect existing services first and preserve existing clusters. The reference target is Ubuntu 26.04 LTS with PostgreSQL 18. Check `apt-cache policy postgresql-18` before installation. If the distribution repository does not supply that major version, configure the [official PostgreSQL Ubuntu APT repository](https://www.postgresql.org/download/linux/ubuntu/) for the host release, then update the package index. Install PostgreSQL 18 only after a package candidate is available. Create distinct, least-privilege application and test roles and databases using interactive password prompts:

```bash
sudo apt-get install --no-install-recommends postgresql-18
sudo -u postgres createuser --pwprompt --no-superuser --no-createdb --no-createrole llama_app
sudo -u postgres createdb --owner=llama_app llama
sudo -u postgres createuser --pwprompt --no-superuser --no-createdb --no-createrole llama_test
sudo -u postgres createdb --owner=llama_test llama_test
sudo -u postgres psql -c 'REVOKE ALL ON DATABASE llama FROM PUBLIC'
sudo -u postgres psql -c 'REVOKE ALL ON DATABASE llama_test FROM PUBLIC'
```

Use distinct generated passwords. For a new dedicated cluster, configure loopback `listen_addresses`, SCRAM host authentication, UTC, and an appropriate connection limit. Review configuration before restarting only the new cluster. Do not change a shared cluster without assessing its users.

Create `.runtime/database.env` privately with mode `0600`. It is ignored, is not supplied by a fresh checkout and is not loaded automatically. Replace both placeholders with distinct passwords, percent-encoding URL-reserved characters:

```text
export LLAMA_DATABASE_URL='postgresql+psycopg://llama_app:PASSWORD@127.0.0.1:5432/llama'
export LLAMA_TEST_DATABASE_URL='postgresql+psycopg://llama_test:OTHER_PASSWORD@127.0.0.1:5432/llama_test'
```

```bash
chmod 600 .runtime/database.env
source .runtime/database.env
alembic upgrade head
alembic current
```

Migrations are explicit; startup does not create tables. Back up existing application data before changing its schema. Database tests require a separate database whose name ends in `_test`; fixtures use temporary isolated schemas. Never point tests at application data. For public deployment, use a separate restricted runtime database role as described in [deployment](deployment.md).

## Start and connect

Keep the following foreground processes running in separate terminals, with `conda activate llama` in each:

```bash
# Terminal 1: native model server
python -m inference.server
```

```bash
# Terminal 2: API and frontend
source .runtime/database.env
python -m backend
```

On the same machine open http://127.0.0.1:8000. For a remote VPS, create a tunnel from your own computer:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 USER@VPS_HOST
```

Keep the tunnel open and visit http://127.0.0.1:8000 on your computer. If local port 8000 is busy, use `-L 18000:127.0.0.1:8000` and open port 18000. Ctrl+C stops each foreground process. PostgreSQL runs separately. The private HTTP profile leaves `LLAMA_PUBLIC_ORIGIN` unset and Secure cookies disabled; the public profile requires HTTPS. `/health` checks backend liveness and `/ready` checks its dependencies. Run one backend worker only.
