# Rezztor EAgent Setup And Deployment

Rezztor EAgent runs as a Docker Compose stack on one host: the application, an
unprivileged runner that executes agent-written code, an Ansible executor for
network automation, SearXNG for web search, and PostgreSQL. A customer installs
it by running `install.sh` from the delivery bundle Rezztor sends, which asks
what it needs and generates the rest; there is no install host to download it
from, because how the product is delivered is decided once it is ready. The k3s appliance, its bootable ISO and the Helm chart
were retired on 2026-09-18; a deployment on Kubernetes is not a supported path.

No code is fetched at runtime and nothing installs itself. An appliance
installed from the ISO asks `update.rezztor.com` once a day which update
packages exist (an administrator can switch that off in Settings, Updates), and
on a paid plan an administrator downloads and installs them from there. A
hand-installed Compose deployment is upgraded by loading the new images and
setting `REZZTOR_VERSION`, then `docker compose up -d`. There is no registry to
pull from: images come from the offline bundle or an update package. Only an
appliance installed from the ISO has the update tool. Durable data lives in two
volumes — `eagent_usr` for
per-user files and the PostgreSQL volume for accounts, settings, conversations,
memory and scheduled tasks — plus `.env` and `runner_key` beside the compose
file, which are in neither and are needed to rebuild the deployment.

One boundary is not in the compose file and has to be applied by hand:
the network guard (`rezztor-guard`, one firewall table on the host) keeps the
runner, the search engine and the cloud executor off the customer's own network,
and a systemd unit loads it before Docker starts. An installation that skips it
starts perfectly and is missing it.

On first start an administrator account is created. Set `REZZTOR_ADMIN_USER`
and `REZZTOR_ADMIN_PASSWORD` to choose the credentials; otherwise a strong
password is generated and printed once to the startup log. Those variables seed
the very first start only — once an account exists they are ignored, so a
forgotten password is a reset rather than an edit to `.env`. Set
`FLASK_SECRET_KEY` to a stable value, or every restart signs all users out.

After first sign-in, configure API keys, chat model, utility model, and embedding model in Settings. Embeddings are required for memory and knowledge recall.

For local development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_ui.py
```

Typical troubleshooting:
- Web UI unreachable: check the container status, port mapping, and startup logs.
- The agent cannot run code: the runner is unreachable, and this is deliberately
  fail-closed rather than falling back to the application's own container.
- Cannot sign in: confirm the account exists and is not disabled.
- Model errors: verify provider, model name, and API key.
- Memory/knowledge not recalling: verify embedding config and reindex if needed.
