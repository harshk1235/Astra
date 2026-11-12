# CI/CD setup (one time)

`.github/workflows/ci.yml` runs on every push:

1. **Tests**: `pytest` against a real Postgres 16 (a GitHub Actions service container).
2. **Docker**: the image must build.
3. **Deploy** (pushes to `main` only, and only if 1 and 2 pass): asks AWS Systems Manager
   to run the `AstraDeploy` document on the EC2 server, which runs `deploy/update.sh <commit>`.
   `update.sh` fast-forwards the server to exactly that commit on `main`, rebuilds, restarts,
   and fails the job if the bot doesn't log "is online" within 2 minutes.

Why Systems Manager instead of SSH: GitHub's runners have changing IPs, so SSH would mean
opening port 22 to the whole internet. With SSM the server keeps SSH closed to everyone but you.

What GitHub is allowed to do: send **one** command, `AstraDeploy`, to **one** server. The
document only accepts a 40-character commit ID and `update.sh` only fast-forwards `main`,
so even a leaked key can do nothing but redeploy code that is already on `main`.

## 1. Let the server talk to Systems Manager

1. IAM → **Roles** → **Create role** → Trusted entity: **AWS service**, Use case: **EC2** → Next.
2. Search and tick **AmazonSSMManagedInstanceCore** → Next → name it `astra-ec2-ssm` → **Create role**.
3. EC2 → Instances → select `astra` → **Actions → Security → Modify IAM role** → choose `astra-ec2-ssm` → **Update IAM role**.
4. Wait ~5 minutes, then Systems Manager → **Fleet Manager**: the instance should be listed as **Online**.
   If not, on the server run `sudo snap restart amazon-ssm-agent` (Ubuntu's AWS images ship the agent).

## 2. Create the deploy command (`AstraDeploy`)

Make sure the console's region (top right) is **Asia Pacific (Mumbai) ap-south-1**.

1. Systems Manager → **Documents** (left menu, under *Shared resources*) → **Create document → Command or Session**.
2. Name: `AstraDeploy` (exactly). Target type: leave empty. Document type: **Command document**.
3. Content: **YAML**, delete the example and paste all of `deploy/astra-deploy-document.yaml` → **Create document**.

## 3. Give GitHub a key that can only run that command

The AWS Free plan has no IAM *Identity providers* page, so GitHub uses a locked-down IAM user.

1. IAM → **Users** → **Create user** → name `astra-github-deploy`, leave
   "Provide user access to the AWS Management Console" **unticked** → Next.
2. Permissions: **Attach policies directly**, tick nothing → Next → **Create user**.
3. Open the user → **Add permissions → Create inline policy → JSON**, paste
   `deploy/github-deploy-policy.json` with `ACCOUNT_ID` (top-right menu, 12 digits, no dashes)
   and `INSTANCE_ID` (EC2 → Instances, starts with `i-`) filled in (both appear twice) →
   Next → name it `deploy-astra` → **Create policy**.
4. Same user → **Security credentials** tab → **Create access key** → use case
   **Third-party service** → tick the confirmation → Next → **Create access key**.
   Copy both values now; the secret is shown only once. Don't put them anywhere but GitHub.

## 4. Tell GitHub

Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | access key from step 3.4 (starts with `AKIA`) |
| `AWS_SECRET_ACCESS_KEY` | secret access key from step 3.4 |
| `EC2_INSTANCE_ID` | the instance ID, `i-...` |

Then the **Variables** tab → **New repository variable**: `DEPLOY_ENABLED` = `true`.
Until it's set, the deploy job is skipped (tests and the Docker build still run).
Optionally also `AWS_REGION` if the server isn't in `ap-south-1`.

## Check it

Push anything to `main` (or Actions → CI/CD → open the latest run → **Re-run all jobs**) and watch
the **Actions** tab: Tests → Docker → Deploy to EC2. The deploy step prints the server's output.
On other branches only the tests and the Docker build run.

## If a key ever leaks

IAM → Users → `astra-github-deploy` → Security credentials → **Deactivate** and delete the key,
create a new one, and update the two GitHub secrets.

## Optional: no stored key (OIDC)

On AWS plans that have IAM → **Identity providers**, you can drop the access key: add the OpenID
Connect provider `https://token.actions.githubusercontent.com` (audience `sts.amazonaws.com`),
create a **Web identity** role limited to `harshk1235/Astra` branch `main` with the same inline
policy, and store its ARN as the secret `AWS_DEPLOY_ROLE_ARN`. When that secret exists the
workflow uses it and ignores the access key secrets.
