# CI/CD setup (one time)

`.github/workflows/ci.yml` runs on every push:

1. **Tests**: `pytest` against a real Postgres 16 (a GitHub Actions service container).
2. **Docker**: the image must build.
3. **Deploy** (pushes to `main` only, and only if 1 and 2 pass): runs
   `deploy/update.sh <commit>` on the EC2 server through AWS Systems Manager.
   `update.sh` moves the server to exactly that commit, rebuilds, restarts, and
   fails the job if the bot doesn't log "is online" within 2 minutes.

Why Systems Manager instead of SSH: GitHub's runners have changing IPs, so
SSH would mean opening port 22 to the whole internet. With SSM the server
keeps SSH closed to everyone but you, and GitHub gets short-lived AWS
credentials through OpenID Connect, so no AWS keys are stored in GitHub.

## 1. Let the server talk to Systems Manager

1. IAM → **Roles** → **Create role** → Trusted entity: **AWS service**, Use case: **EC2** → Next.
2. Search and tick **AmazonSSMManagedInstanceCore** → Next → name it `astra-ec2-ssm` → **Create role**.
3. EC2 → Instances → select `astra` → **Actions → Security → Modify IAM role** → choose `astra-ec2-ssm` → **Update IAM role**.
4. Wait ~5 minutes, then Systems Manager → **Fleet Manager**: the instance should be listed as **Online**.
   If not, on the server run `sudo snap restart amazon-ssm-agent` (Ubuntu's AWS images ship the agent).

## 2. Let GitHub deploy (and only GitHub, only from main)

1. IAM → **Identity providers** → **Add provider** → **OpenID Connect**
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com` → **Add provider**.
2. IAM → **Roles** → **Create role** → Trusted entity: **Web identity**
   - Identity provider: `token.actions.githubusercontent.com`, Audience: `sts.amazonaws.com`
   - GitHub organization: `harshk1235`, Repository: `Astra`, Branch: `main` → Next.
   - Skip the permissions list → Next → name it `astra-github-deploy` → **Create role**.
3. Open the new role → **Add permissions → Create inline policy → JSON**, paste
   `deploy/github-deploy-policy.json` with `ACCOUNT_ID` (top-right menu in the console,
   12 digits, no dashes) and `INSTANCE_ID` (EC2 → Instances, starts with `i-`) filled in →
   name it `deploy-astra` → **Create policy**.
4. Copy the role's **ARN** (looks like `arn:aws:iam::123456789012:role/astra-github-deploy`).

## 3. Tell GitHub

Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | the role ARN from step 2.4 |
| `EC2_INSTANCE_ID` | the instance ID, `i-...` |

Then the **Variables** tab → **New repository variable**: `DEPLOY_ENABLED` = `true`.
Until it's set, the deploy job is skipped (tests and the Docker build still run).
Optionally also `AWS_REGION` if the server isn't in `ap-south-1`.

## Check it

Push anything to `main` (or Actions → CI/CD → **Re-run all jobs** on the latest run) and watch
the **Actions** tab: Tests → Docker → Deploy to EC2. The deploy step prints the server's output.
On other branches only the tests and the Docker build run.
