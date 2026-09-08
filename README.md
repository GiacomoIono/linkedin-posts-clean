# LinkedIn Posts Clean

This project takes your latest LinkedIn post and turns it into a Webflow blog post.

The main publishing command first resumes every unfinished Webflow verification checkpoint, including posts that are no longer in LinkedIn's 48-hour window. Its normal flow does this:

1. Looks for your newest LinkedIn post from the last 48 hours.
2. Turns the post text into simple blog-post HTML.
3. Finds matching source images directly inside the top-level `images/` folder.
4. If no date-matched top-level source file exists, uses OpenAI to create one reviewed 16:9 PNG fallback under `images/generated/`.
5. Uploads local image files through Webflow's Assets API and verifies the public Webflow URLs.
6. Uses OpenAI to create the headline, summary, and missing image ALT text from those Webflow URLs.
7. Researches whether the body needs authoritative evidence links and safely adds zero or more.
8. Sends the post to the Webflow Blog Posts collection and verifies the saved body and image fields.
9. Saves a record of what happened in the `data/` folder, including a reusable Webflow asset cache.

The repository can be private. GitHub stores the source files and runs the automation; Webflow hosts the images that readers and OpenAI need to access. Uploaded Webflow images remain public even when the repository is private.

## Set up this project on a MacBook Pro

This is the complete one-time setup and migration guide for a MacBook Pro. Follow it from top to bottom. Shared instructions apply to both your M4 and Intel models; the few hardware-specific differences are clearly labelled.

The project uses:

- Git and GitHub for version control.
- Python 3.11 installed through Homebrew, matching the version used by GitHub Actions.
- A local Python virtual environment named `.venv`.
- The packages pinned in `requirements.txt`.
- A local `.env` file for API credentials.
- Visual Studio Code is assumed to be installed already; this guide only checks its optional `code` terminal command.
- No Node.js, `npm`, Docker, database, or Webflow CLI.

### What moves automatically and what does not

| Item | Already stored remotely? | What to do on the new MacBook Pro |
| --- | --- | --- |
| Tracked code, tests, prompts, images, and `data/` files | Yes, in GitHub | Clone the repository. |
| GitHub Actions workflows and their schedule | Yes, in GitHub | Nothing. They continue running even while the old Mac is off. |
| GitHub Actions secrets and variables | Yes, in GitHub | Normally nothing. Their values cannot be viewed after saving, but they stay attached to the repository. |
| Local `.env` file | No | Transfer it securely from the old Mac or create new credentials. |
| Local `.venv` folder | No, and it should never be transferred | Recreate it and reinstall `requirements.txt`. |
| GitHub login on the computer | No | Authenticate the new MacBook Pro with GitHub CLI. |
| Git configuration and repository Git hook setting | No | Configure them again. |
| Uncommitted or untracked files | No | Commit and push them, or transfer them separately before retiring the old Mac. |

### Part 0: prepare the old Mac before replacing it

Do this while the old Mac is still available.

1. Open Terminal on the Mac.
2. Go to the existing repository folder. Replace the example path if the project is stored elsewhere:

~~~bash
cd ~/Documents/GitHub/linkedin-posts-clean
~~~

3. Confirm that this is the correct repository:

~~~bash
git remote -v
git branch --show-current
git status
~~~

The remote should contain:

~~~text
https://github.com/GiacomoIono/linkedin-posts-clean.git
~~~

4. Read the `git status` output carefully.

   - `nothing to commit, working tree clean` means all tracked work is already committed.
   - `modified` means a tracked file has local changes.
   - `untracked` means a local file has never been added to Git.
   - If you see changes you want to keep, commit and push them before continuing.
   - Do not use `git reset --hard` or delete files merely to make the status clean.

5. Once the working tree is clean, update and push `main`:

~~~bash
git switch main
git pull --ff-only origin main
git push origin main
git status
~~~

6. Open the repository on GitHub and verify that the latest files and commit are visible:

   [https://github.com/GiacomoIono/linkedin-posts-clean](https://github.com/GiacomoIono/linkedin-posts-clean)

7. Save the local `.env` file securely.

   - It is hidden by default because its name starts with a dot.
   - On macOS Finder, press `Command + Shift + .` to show or hide hidden files.
   - Use an encrypted password manager, an encrypted drive, or another secure transfer method.
   - Do not email it to yourself, paste it into chat, store it in a public cloud note, or commit it to GitHub.

8. Check whether any other local-only files need to be kept:

~~~bash
git status --short --untracked-files=all
~~~

Do not copy the old `.venv` folder, `__pycache__` folders, or the entire old `.git` folder. A fresh clone and a fresh virtual environment are safer and less likely to carry broken machine-specific files.

### Part 1: install the required software on the MacBook Pro

These instructions work on both of your MacBook Pros. The M4 model uses Apple Silicon; the older model uses an Intel processor. Most commands are identical. The main difference is the processor architecture and the folder where Homebrew is installed.

Before installing anything, open `Apple menu  > System Settings > General > Software Update` on each Mac and install the newest macOS version Apple offers for that model. This can differ: the M4 supports newer macOS versions, while the maximum version available to the Intel Mac depends on its year. If Homebrew later warns that the Intel Mac's macOS version is unsupported, stop and review the warning instead of forcing the installation.

1. Open `Terminal` from `Applications > Utilities`, or press `Command + Space`, type `Terminal`, and press Enter.

2. Identify which Mac you are using.

Open the Apple menu ` > About This Mac`:

- On the M4 MacBook Pro, the window shows `Chip: Apple M4`.
- On the Intel MacBook Pro, the window shows `Processor` followed by an Intel processor name.

Then run:

~~~bash
uname -m
~~~

Expected result:

| MacBook Pro | Result from `uname -m` | Homebrew's normal location |
| --- | --- | --- |
| M4 | `arm64` | `/opt/homebrew` |
| Intel | `x86_64` | `/usr/local` |

Important for the M4: if `uname -m` unexpectedly prints `x86_64`, that Terminal session is running through Rosetta. Close it and open a normal native Terminal before installing Homebrew or Python. This project does not require Rosetta.

3. Install Apple's command-line tools:

~~~bash
xcode-select --install
~~~

If macOS says they are already installed, continue. Otherwise, approve the installation and wait for it to finish. This command is the same on M4 and Intel.

4. Install Homebrew, the package manager used by the commands below.

   - Open [https://brew.sh/](https://brew.sh/).
   - Confirm that its official installation command still matches the following command.
   - Paste it into Terminal and press Enter:

~~~bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
~~~

5. At the end, Homebrew prints a `Next steps` section with commands that add Homebrew to the shell path. Copy and run the exact commands shown on that Mac.

   - On the M4 MacBook Pro, those commands normally reference `/opt/homebrew/bin/brew`.
   - On the Intel MacBook Pro, Homebrew normally uses `/usr/local/bin/brew`.
   - Do not copy the M4 path onto the Intel Mac or the Intel path onto the M4 Mac.

6. Close Terminal, reopen it, and verify the architecture and Homebrew installation:

~~~bash
uname -m
command -v brew
brew --prefix
brew --version
~~~

Expected results:

| Check | M4 MacBook Pro | Intel MacBook Pro |
| --- | --- | --- |
| `uname -m` | `arm64` | `x86_64` |
| `command -v brew` | `/opt/homebrew/bin/brew` | Usually `/usr/local/bin/brew` |
| `brew --prefix` | `/opt/homebrew` | `/usr/local` |

If the architecture and Homebrew prefix do not match the same column, stop before continuing. This can indicate an old Intel Homebrew installation being used through Rosetta on the M4.

7. Install Git, GitHub CLI, and Python 3.11 through Homebrew:

~~~bash
brew update
brew install git gh python@3.11
~~~

This guide deliberately uses Homebrew as the only Python installation method. Do not also install Python 3.11 from `python.org`, Anaconda, or another package manager for this project.

The command is identical on both Macs. Homebrew automatically installs the correct build for the active processor.

8. Close and reopen Terminal again, then verify the installations:

~~~bash
git --version
gh --version
python3.11 --version
brew list --versions python@3.11
command -v python3.11
~~~

Expected results:

- The Python version begins with `Python 3.11`.
- `brew list --versions python@3.11` prints the installed Homebrew formula and version.
- On the M4, `command -v python3.11` normally starts with `/opt/homebrew/`.
- On the Intel Mac, `command -v python3.11` normally starts with `/usr/local/`.

A later Python version may be installed elsewhere on the Mac, but always use the Homebrew Python 3.11 installation for this project because it matches GitHub Actions.

The virtual environment created later is machine-specific. Create a separate `.venv` on each MacBook Pro; never copy the M4 `.venv` to the Intel Mac or the Intel `.venv` to the M4 Mac.

Visual Studio Code is assumed to be installed already. Check whether its optional terminal command is available:

~~~bash
code --version
~~~

If `code` is not recognised:

1. Open Visual Studio Code.
2. Press `Command + Shift + P`.
3. Search for `Shell Command: Install 'code' command in PATH`.
4. Select it.
5. Close and reopen Terminal.

Official references:

- [Identify whether a Mac uses Apple Silicon or Intel](https://support.apple.com/en-us/116943)
- [Homebrew installation and processor-specific prefixes](https://docs.brew.sh/Installation)
- [Homebrew Python 3.11 formula](https://formulae.brew.sh/formula/python@3.11)
- [Git for macOS](https://git-scm.com/install/mac)
- [GitHub CLI](https://cli.github.com/)

### Part 2: authenticate the new MacBook Pro with GitHub

A private repository requires authentication for cloning, pulling, and pushing. Complete this login before cloning, using an account that has access to the repository. GitHub account passwords cannot be used as Git passwords. GitHub CLI will configure secure HTTPS authentication.

1. Start the login:

~~~bash
gh auth login
~~~

2. Choose these options:

   - `GitHub.com`
   - `HTTPS`
   - `Yes` when asked whether Git should use your GitHub credentials
   - `Login with a web browser`

3. Copy the one-time code shown in the terminal, press Enter, sign in through the browser, and approve GitHub CLI.

4. Finish Git's credential configuration and verify the account:

~~~bash
gh auth setup-git
gh auth status
~~~

The output should show that you are logged in to `github.com` as the account that owns or can write to `GiacomoIono/linkedin-posts-clean`.

5. Configure the name and email attached to future commits. Replace the email placeholder with an email verified on your GitHub account, or your GitHub private `noreply` address from [GitHub email settings](https://github.com/settings/emails):

~~~bash
git config --global user.name "Giacomo Iotti"
git config --global user.email "YOUR_VERIFIED_GITHUB_EMAIL"
~~~

6. Verify what Git saved:

~~~bash
git config --global --get user.name
git config --global --get user.email
~~~

Never put an OpenAI, LinkedIn, or Webflow API token into Git's username, email, or remote URL.

### Part 3: clone a fresh copy of the repository

Do not use GitHub's `Download ZIP` button. A ZIP does not contain the Git history and cannot use `git pull` or `git push`.

~~~bash
mkdir -p ~/Documents/GitHub
cd ~/Documents/GitHub
git clone https://github.com/GiacomoIono/linkedin-posts-clean.git
cd linkedin-posts-clean
~~~

#### Check the clone

~~~bash
git remote -v
git branch --show-current
git status
git pull --ff-only origin main
~~~

Expected results:

- `origin` points to `https://github.com/GiacomoIono/linkedin-posts-clean.git`.
- The branch is `main`.
- Git says the branch is up to date with `origin/main`.
- Git says `nothing to commit, working tree clean`.

A fresh `git clone` already downloads the latest committed version. The explicit `git pull --ff-only origin main` is included so you also know the command to use later.

### Part 4: enable the repository's large-file protection

The repository contains `.githooks/pre-commit`. It blocks files larger than 90 MB before they are committed.

Run this once inside the repository:

~~~bash
git config core.hooksPath .githooks
~~~

On macOS, also make sure the hook is executable:

~~~bash
chmod +x .githooks/pre-commit
~~~

Verify the setting:

~~~bash
git config --get core.hooksPath
~~~

It should print:

~~~text
.githooks
~~~

### Part 5: create and activate the Python virtual environment

A virtual environment is an isolated Python installation for this project. It prevents this project's package versions from interfering with packages used by other projects.

The `.venv` folder is local and must not be committed. Before creating it, exclude it locally from Git.

~~~bash
printf "\n# Local Python virtual environment\n.venv/\n" >> .git/info/exclude
python3.11 -m venv .venv
source .venv/bin/activate
~~~

After activation, the terminal prompt normally starts with `(.venv)`.

Verify that the active Python belongs to the virtual environment:

~~~bash
python --version
which python
~~~

The version must begin with `Python 3.11`, and the path should contain `linkedin-posts-clean/.venv`.

### Part 6: install the Python packages

Keep the virtual environment active. Then run:

~~~bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip --version
~~~

`requirements.txt` is the source of truth. It currently installs pinned versions of:

- `openai`
- `Pillow`
- `python-dotenv`
- `requests`

Do not use `sudo pip install`. Do not install these packages globally. Do not run `npm install`; this repository has no Node.js packages.

Whenever `requirements.txt` changes after a future `git pull`, reactivate `.venv` and run:

~~~bash
python -m pip install -r requirements.txt
~~~

### Part 7: restore or create the local environment variables

The local `.env` file contains secrets and is deliberately excluded from Git. It will not arrive with `git clone`.

1. Copy the tracked example to `.env`, next to `README.md` and `requirements.txt`, then open it.

~~~bash
cp .env.example .env
code .env
~~~

If the `code` command is unavailable, open `.env` from Visual Studio Code. Keep `.env.example` unchanged; it is the tracked template and must never contain real credentials, including in a private repository.

2. Add the real values after each required equals sign:

| Local variable | Required? | Where the value comes from |
| --- | --- | --- |
| `LINKEDIN_ACCESS_TOKEN` | Yes | Secure copy of the old `.env` value, or a newly authorised token from the LinkedIn developer application. |
| `OPENAI_API_KEY` | Yes | Secure copy of the old value, or a new key from the same OpenAI API project. Existing key values usually cannot be revealed again after creation. |
| `OPENAI_MODEL` | No | Defaults to `gpt-5.6-sol` for concept planning, semantic image review, SEO metadata, evidence-link research, and ALT text. |
| `OPENAI_IMAGE_MODEL` | No | Defaults to `gpt-image-2`; change it only when the fallback-image pipeline is intentionally updated. |
| `WEBFLOW_API_TOKEN` | Yes, unless the alternative is set | A Webflow token with CMS read/write and Assets read/write access to the target site. Site settings access is not required. |
| `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` | Alternative | Supported in place of `WEBFLOW_API_TOKEN`; leave it blank when the primary variable is set. |
| `WEBFLOW_SITE_ID` | Yes for image uploads | The Webflow site that owns the Blog Posts collection and should receive the images. This ID is configuration, not a secret. |
| `WEBFLOW_COLLECTION_ID` | No | Defaults to the Blog Posts collection ID shown in `.env.example`. |
| `WEBFLOW_PUBLISH` | No | Defaults to `true`. Read the live-run warning below before changing or running it. |
| `LINKEDIN_PROMPT_PROFILE` | No | Leave blank to use the first enrichment profile in `config/prompts.json`. |
| `FORCE_WEBFLOW_SYNC` | No | Defaults to `false`; keep it false during normal use. |

Do not add spaces around the equals sign. Do not wrap the tokens in quotation marks unless a value genuinely contains spaces.

#### Local secrets and GitHub Actions secrets are separate

The scheduled GitHub Action currently reads:

- `LINKEDIN_ACCESS_TOKEN` from a GitHub Actions secret.
- `OPENAI_API_KEY` from a GitHub Actions secret.
- `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` from a GitHub Actions secret only.
- `WEBFLOW_SITE_ID` from a GitHub Actions repository variable.

The workflow keeps the existing Blog Posts collection ID, `63250855178122098387d7ef`. The site variable must identify the site that owns that collection. The production preflight stops before image generation if a required secret or site variable is missing; it prints names only, never credential values.

Changing `.env` does not update GitHub. Before the first GitHub run with asset uploads, update the `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` secret with the token that has CMS and Assets read/write access, and add `WEBFLOW_SITE_ID` under the repository's **Settings → Secrets and variables → Actions → Variables**. Tokens belong under **Secrets**, not **Variables**. Remove any old repository variable containing the Webflow token once the secret is configured. Site settings permission is unnecessary because the pipeline uploads assets and publishes CMS items without publishing the entire site. See [Webflow's asset upload API](https://developers.webflow.com/data/reference/assets/assets/create).

Those remote values remain in GitHub when you change MacBook. You do not need to recreate them merely because you cloned the repository elsewhere. GitHub intentionally hides saved secret values; it will not let you copy them back out for the local `.env` file.

If the old `.env` is unavailable, create or rotate the required credentials in the relevant services. Do not weaken security by trying to extract hidden GitHub Actions secrets.

3. Save `.env` and verify that Git ignores both local-only items:

~~~bash
git check-ignore -v .env
git check-ignore -v .venv/
git status --short
~~~

The first two commands should show an ignore rule. The final command should not list `.env` or `.venv` and should normally print nothing.

4. Confirm that the application can see the required settings without printing the secret values:

~~~bash
python -c "from pipeline.config import load_config; c=load_config(); print('LinkedIn configured:', bool(c.linkedin_access_token)); print('OpenAI configured:', bool(c.openai_api_key)); print('Webflow configured:', bool(c.webflow_api_token)); print('Webflow site configured:', bool(c.webflow_site_id))"
~~~

Expected results for the normal configuration:

~~~text
LinkedIn configured: True
OpenAI configured: True
Webflow configured: True
Webflow site configured: True
~~~

### Part 8: run the safe local verification

Run the repository's unit tests:

~~~bash
python -m unittest discover -s tests -v
~~~

The final line should be:

~~~text
OK
~~~

The exact number of tests may increase over time. `OK` is the important result.

These tests are the correct first verification because external service calls are mocked. They make no paid API calls and do not write to Webflow.

For an optional read-only LinkedIn check:

1. Open the repository's [Actions tab](https://github.com/GiacomoIono/linkedin-posts-clean/actions).
2. Select `Validate LinkedIn Fetch`.
3. Select `Run workflow`.
4. Keep the branch set to `main` and start it.
5. Open the run and confirm that all steps are green.

That workflow runs the tests and calls the LinkedIn API read-only. It does not run the Webflow publishing pipeline.

The same manual form has a `live_image_smoke` option. Leave it off for normal validation. If you deliberately enable it, the workflow uses the existing `OPENAI_API_KEY` secret to create one paid PNG preview, saves it as a one-day GitHub Actions artifact, and never commits it or sends it to Webflow.

### Part 9: understand the live-run warning

Do not run the following command merely to check whether installation worked:

~~~bash
python -m pipeline.main
~~~

It is a live end-to-end command. With valid credentials, it can:

- read recent LinkedIn activity;
- call the OpenAI API and incur API usage;
- upload local source images or a prepared generated fallback to Webflow Assets;
- create or update a Webflow CMS item;
- publish that item when `WEBFLOW_PUBLISH=true`;
- write output files under `data/`.

Important: `WEBFLOW_PUBLISH=false` is not a complete dry-run mode. It prevents the final publish step, but the pipeline can still upload public Webflow assets and create or update a Webflow draft item.

The normal scheduled GitHub Action already runs the production pipeline. A local live run is optional and should be used only when you intentionally want to process a real LinkedIn post.

### Part 10: perform the first intentional live run

Only do this when all of the following are true:

- There is a real LinkedIn post inside the rolling 48-hour window.
- The local `.env` values are configured.
- `FORCE_WEBFLOW_SYNC=false` unless a maintenance override is deliberately required.
- Any matching image has the correct date-based filename.
- Any matching image is available in this local checkout's top-level `images/` folder.
- For a post without matching source files, `python -m pipeline.prepare_image` has prepared or reused a reviewed generated fallback locally.

Local runs upload the file bytes directly from this checkout; a local source image does not need to be public or already committed. GitHub Actions can only use files available in its checkout, so commit and push source images to `main` before a scheduled run. The generated-image preparation command can call paid OpenAI APIs; it creates the reviewed PNG and manifest without uploading an image or writing a CMS item.

Activate the environment if needed:

~~~bash
cd ~/Documents/GitHub/linkedin-posts-clean
source .venv/bin/activate
~~~

Then run:

~~~bash
python -m pipeline.main
~~~

Normal outcomes:

- If Webflow already contains the same live LinkedIn URL, the pipeline stops before enrichment or Webflow writes.
- If no qualifying LinkedIn post exists within 48 hours, it prints `No recent LinkedIn posts found`. It exits with code `2` when there was no pending recovery, or `0` if it successfully recovered an older pending item first. GitHub Actions treats both as successful runs.
- If a new post exists, the pipeline can enrich it and write it to Webflow according to the `.env` settings.

After any intentional local run, inspect what changed:

~~~bash
git status
git diff -- data
~~~

Do not commit or discard unexpected output until you understand it.

### Part 11: use this start-of-work routine every time

~~~bash
cd ~/Documents/GitHub/linkedin-posts-clean
git switch main
git status
git pull --ff-only origin main
source .venv/bin/activate
git log -1 --oneline
~~~

Read `git status` before pulling:

- If it says the working tree is clean, continue.
- If it lists modified or untracked files, stop and decide whether those changes should be committed, moved, or kept for later.
- Do not force a pull over local changes.

After pulling, reinstall packages only if `requirements.txt` changed:

~~~bash
python -m pip install -r requirements.txt
~~~

### Part 12: make and publish a normal code or documentation change

1. Start from an up-to-date `main` branch.
2. Create a separate branch. Replace `short-description` with a few lowercase words describing the task:

~~~bash
git switch -c giacomo/short-description
~~~

3. Make the change in Visual Studio Code:

~~~bash
code .
~~~

4. Run the tests:

~~~bash
python -m unittest discover -s tests -v
~~~

5. Inspect the changed files:

~~~bash
git status
git diff
~~~

6. Stage only the files that belong to the change. Examples:

~~~bash
git add README.md
git add pipeline/linkedin.py tests/test_linkedin.py
git add images/2026-08-17.jpg
~~~

Avoid `git add .` until you are comfortable reviewing every file it would stage.

7. Commit and push. Replace the examples with the real description and branch name:

~~~bash
git commit -m "docs: describe the change"
git push -u origin giacomo/short-description
~~~

8. Open a pull request:

~~~bash
gh pr create --fill --web
~~~

9. After the pull request is merged, return to `main` and download the merged result:

~~~bash
git switch main
git pull --ff-only origin main
~~~

10. When finished working, leave the virtual environment:

~~~bash
deactivate
~~~

### Part 13: troubleshooting

#### The M4 reports `x86_64` or Homebrew uses `/usr/local`

That normally means the M4 Terminal session or Homebrew installation is using Intel emulation through Rosetta. Do not continue installing project packages in that environment.

Close Terminal, open a normal native Terminal, and run:

~~~bash
uname -m
command -v brew
brew --prefix
~~~

On the M4, the expected results are `arm64`, `/opt/homebrew/bin/brew`, and `/opt/homebrew`. Do not delete an existing `/usr/local` installation until you understand whether another application still uses it.

#### `brew: command not found`

Reopen [https://brew.sh/](https://brew.sh/), then rerun the shell-path commands printed under Homebrew's `Next steps`. Close and reopen Terminal afterwards.

#### `python3.11: command not found` on macOS

Run:

~~~bash
brew install python@3.11
brew info python@3.11
~~~

Then close and reopen Terminal.

#### The terminal does not show `(.venv)`

The environment is not active. Run the activation command again:

~~~bash
source .venv/bin/activate
~~~


#### `ModuleNotFoundError`

Confirm that `.venv` is active, then run:

~~~bash
python -m pip install -r requirements.txt
~~~

#### `fatal: not a git repository`

The terminal is in the wrong folder. Run `pwd`, then change into the `linkedin-posts-clean` folder.

#### GitHub authentication fails during `git push`

Run:

~~~bash
gh auth status
gh auth login
gh auth setup-git
~~~

Use the `GiacomoIono` GitHub account or another account with write permission to the repository.

#### `git pull --ff-only` refuses to continue

Run:

~~~bash
git status
~~~

Do not use a force command or `git reset --hard`. Local work, a branch mismatch, or diverging commits need to be understood first. Preserve the output and ask for help.

#### `.env` is missing after cloning

This is expected. It is intentionally excluded from Git. Restore it securely from the old Mac or generate new credentials.

#### A required token reports `missing`, `401`, or `403`

Check that:

- the correct value is in the local `.env` file;
- there are no spaces around the equals sign;
- the token has not expired or been revoked;
- the token belongs to the correct LinkedIn, OpenAI, or Webflow account;
- the Webflow token can read and write CMS items and Assets on the configured site;
- `WEBFLOW_SITE_ID` identifies the site that owns the configured Blog Posts collection;
- for GitHub runs, the updated token is saved as the `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` Actions secret and the site ID as the `WEBFLOW_SITE_ID` repository variable.

Do not print the full token in Terminal screenshots or support messages.

#### A commit is blocked because a file exceeds 90 MB

The repository hook is protecting GitHub from a large binary. Do not bypass it casually. Remove the large file from the staged change or use an appropriate external storage strategy.

#### An image exists locally but is missing from Webflow

Check these conditions:

1. Its filename begins with the LinkedIn publication date in `YYYY-MM-DD` format.
2. Its extension is `.jpg`, `.jpeg`, `.png`, or `.webp`.
3. The file is directly inside `images/` in the checkout running the command. For a scheduled GitHub run, it must be committed on `main`.
4. The Webflow token has Assets read/write permissions and `WEBFLOW_SITE_ID` is configured for the correct site.
5. The asset upload and public Webflow URL validation succeeded. Check the failure message; a missing or invalid image stops the post instead of silently removing it from the gallery.

#### The pipeline says no recent LinkedIn post exists

The lookup window is a rolling 48 hours, not two calendar days. If the post is older, this is expected.

### Final MacBook Pro setup checklist

- [ ] `uname -m` reports `arm64` on the M4 or `x86_64` on the Intel Mac.
- [ ] `brew --prefix` reports `/opt/homebrew` on the M4 or `/usr/local` on the Intel Mac.
- [ ] Git is installed.
- [ ] Python 3.11 is installed through Homebrew.
- [ ] GitHub CLI is installed and authenticated.
- [ ] Git commit name and email are configured.
- [ ] A fresh repository clone exists.
- [ ] `main` is up to date with `origin/main`.
- [ ] The repository's `.githooks` path is enabled.
- [ ] The local `.venv` exists and is active.
- [ ] `requirements.txt` is installed.
- [ ] The local `.env` exists and contains the required credentials.
- [ ] `.env` and `.venv` are ignored by Git.
- [ ] The configuration check reports the three required services and the Webflow site as configured.
- [ ] The unit tests finish with `OK`.
- [ ] The scheduled GitHub Action is still enabled.
- [ ] You understand that `python -m pipeline.main` is a live command, not a harmless setup test.

## Quick Start (after the one-time setup)

This shorter routine assumes the repository has already been cloned, the `.venv` environment and `.env` file already exist, and the full MacBook Pro setup guide above has been completed.

Run:

~~~bash
cd ~/Documents/GitHub/linkedin-posts-clean
git switch main
git status
git pull --ff-only origin main
source .venv/bin/activate
~~~

Run the safe tests:

~~~bash
python -m unittest discover -s tests -v
~~~

Only when you intentionally want to run the live LinkedIn-to-Webflow pipeline:

~~~bash
python -m pipeline.main
~~~

Remember that this final command can call paid APIs and write or publish a Webflow CMS item.

## Safe SEO-only live preview

Use the SEO-only preview when you want to test the current SEO prompt with the real OpenAI API without running the full publishing pipeline.

The command reads the saved post in `data/last_linkedin_post.json` by default. It calls OpenAI to generate only the title and meta description. It does not:

- call LinkedIn;
- call Webflow;
- generate image ALT text;
- change or create any local file.

The OpenAI request is live and may incur a small API charge.

With the virtual environment active, run one preview:

~~~bash
python -m pipeline.seo_preview
~~~

The output shows the source post, model, generated metadata and exact character counts.

To check consistency across several independent generations of the same saved post, use `--runs`. For example:

~~~bash
python -m pipeline.seo_preview --runs 10
~~~

The safety limit is 20 runs per command. Every run makes a separate OpenAI API request.

To test another saved post JSON file without changing the default data file, pass its path:

~~~bash
python -m pipeline.seo_preview --post /path/to/post.json
~~~

The JSON file must contain a complete, non-empty `content` field. The body and any supplied `imageContext` must provide at least five words in total; thinner source material is rejected instead of being padded with invented claims. A saved `url` is optional and displayed only for reference. The preview also accepts `currentTitle`, `currentDescription`, and `targetKeyword` when that context is available.

## The Important Settings

Most days, these are the only settings you need to care about:

| Setting | What it does |
| --- | --- |
| `LINKEDIN_ACCESS_TOKEN` | Lets the script read your recent LinkedIn activity. |
| `OPENAI_API_KEY` | Lets the script write metadata, research evidence links, create ALT text, and produce a missing-image fallback. |
| `OPENAI_MODEL` | Uses `gpt-5.6-sol` for image concept planning and review, SEO metadata, evidence-link research, and ALT text. |
| `OPENAI_IMAGE_MODEL` | Selects the image model; the default is `gpt-image-2`. |
| `WEBFLOW_API_TOKEN` | Lets the script upload/read Webflow assets and create, update, and publish CMS posts. Requires CMS and Assets read/write access. |
| `WEBFLOW_SITE_ID` | Identifies the site that receives image uploads; must own the configured collection. |
| `WEBFLOW_PUBLISH` | When `true`, Webflow items are published after they are written. |

`WEBFLOW_READ_AND_WRITE_BLOG_POSTS` can also be used instead of `WEBFLOW_API_TOKEN`.

## LinkedIn Window

The LinkedIn scraper looks back exactly 48 hours from the time the script runs.

That means it is a rolling time window, not "today and yesterday" as calendar days. For example, if the script runs at 04:00 on June 3, it searches back to 04:00 on June 1.

LinkedIn is queried in pages of 50 changelog records. The scraper follows every page until it reaches the end of the 48-hour window, and it stops if a page contains only older records. A temporary LinkedIn `500` response is retried twice with a short backoff before the run fails.

If no LinkedIn post is found in that window and there was no pending recovery work, the script exits cleanly with code `2`. The GitHub Action treats that as "nothing to do", not as a failure. If it successfully recovers an older pending Webflow item before finding no recent post, it exits with code `0` and keeps the completed recovery state.

## Images

Put images in the `images/` folder and name them by the LinkedIn post date.

For one image:

```text
images/2026-06-01.jpg
```

For multiple images:

```text
images/2026-06-01_1.jpg
images/2026-06-01_2.jpg
images/2026-06-01_3.jpg
```

Supported formats are `.jpg`, `.jpeg`, `.png`, and `.webp`.

The uploader inspects the actual file contents. A PNG accidentally named `.jpg` is uploaded with a `.png` filename and the correct content type; the local filename, original bytes and gallery order stay unchanged. Multi-picture JPEG files (MPO) stored as `.jpg` or `.jpeg` are supported too. Files must decode successfully and fit Webflow's 4 MB image-upload limit.

Important: the current pipeline does not download media directly from LinkedIn. In this project, a "source image" means a date-matched file already present directly at the top level of `images/`. Discovery is deliberately non-recursive, so files inside `images/generated/` are never mistaken for LinkedIn source media. This folder is the sole image source of truth: when no matching top-level file exists, the pipeline generates a fallback even if LinkedIn's metadata reports media.

When there are multiple images, the number decides the order. `_1` is first, `_2` is second, and so on. When there is only one image, the filename can just be the date.

The pipeline sends:

- all images to Webflow's `post-images` field, in the right order;
- the first image to `main-image`;
- the first image to `thumbnail-image`;
- an `alt` value for every image.

The pipeline uploads the local file bytes to Webflow before OpenAI enrichment or the CMS write. Neither Webflow nor OpenAI needs access to this repository. GitHub Actions still needs source files committed on `main` so its authenticated checkout contains them; local runs can use local files directly.

### Webflow asset uploads and recovery

For each source image or generated fallback, the pipeline checks the local file, calculates a content fingerprint, and consults `data/webflow_assets.json`. A usable cached asset is reused after checking that its public Webflow URL serves the expected image. New uploads use the Assets API to request upload details, then send the image bytes to Webflow's storage. An asset entry alone is not proof that the image was uploaded. See [Webflow's asset upload API](https://developers.webflow.com/data/reference/assets/assets/create).

The cache records the site, file fingerprint, original and upload filenames, Webflow asset ID and hosted URL when available, and upload status. It saves a `creating` record before requesting an asset entry, a `pending` record after receiving the asset ID, and a `ready` record only after verifying the public bytes. An explicitly rejected creation is recorded as `create_rejected` and can be retried. Keep this file alongside the source files and generated-image manifest. Tokens and temporary upload credentials are never stored in it. Identical files reuse the verified cached asset for the same site; changed content requires a new upload.

If an asset-creation response is lost, the next run lists the site's assets and matches the saved upload filename before considering another creation. It verifies any recovered asset's public bytes. Existing metadata with a missing, incomplete or changed public file stops the run instead of creating another asset automatically. Inspect that specific asset and restore its file, or explicitly remove confirmed incomplete metadata and its matching cache entry before retrying. Do not clear the cache just to bypass an uncertain upload; it is the record that prevents duplicate creation.

For source images, the original gallery order and ALT text remain attached to their corresponding images. For a generated fallback, the reviewed PNG and its reviewed ALT text remain the main image only. Upload errors, invalid files, unavailable hosted images and missing image ALT text stop the pipeline before it can publish an incomplete replacement.

### OpenAI fallback for a post without a source image

When there is no date-matched top-level source file, the production workflow:

1. Checks again that Webflow does not already have the LinkedIn URL, avoiding an unnecessary paid image request.
2. Uses `gpt-5.6-sol` to commit to exactly one article-specific concept and select exactly three distinct bundled style references. At least one chosen reference is human-centered.
3. Makes exactly one `gpt-image-2` image call requesting one exact `1536 x 864` PNG, with automatic SDK retries disabled.
4. Rejects any sole raw result that is not a decodable exact-16:9 PNG, then uses `gpt-5.6-sol` for semantic review before resizing or compression. That same vision review writes final ALT text from both the post context and the content actually visible in the rendered image. Preparation preserves the reviewed composition and never crops it. A failed review or empty final ALT saves nothing and does not generate a replacement in the same run.
5. Prepares exactly one full-bleed PNG at an exact 16:9 ratio, preferring `1200 x 675`, and enforces a maximum size of 800,000 bytes.
6. Saves it only under `images/generated/` with a stable filename containing the publication date, a descriptive slug, and a LinkedIn URL hash.
7. Records its checksum, models, concept, quality review, references, prompt, dimensions, byte count, and ALT text in `data/generated_main_images.json`.
8. Commits and pushes only `images/generated/` and the manifest before the CMS step, preserving the reviewed result for a later retry. `pipeline.main` uploads that local PNG through Webflow Assets and checks the hosted file before the CMS write.

For example, a generated file can be named `images/generated/2026-08-25-ai-changes-product-discovery-a1b2c3d4e5.png`. The URL hash prevents two image-less posts published on the same date from colliding.

For a generated fallback, Webflow receives:

- the PNG in `main-image` only;
- no value in `post-images`;
- no value in `thumbnail-image`.

If a discovered source image cannot be read, or if image generation, validation, asset upload, hosted-image verification, or the pre-Webflow Git push fails, the workflow stops. It does not publish an image-less replacement post.

## Evidence Links

Every new post passes through an evidence-link stage after its headline, summary, ALT text, and image attachment are complete. This stage does not rewrite the post and does not call Webflow itself.

The stage:

1. Decides whether the body contains material claims that warrant sources. Opinions and personal experiences can correctly receive zero links.
2. Searches the live web, opens candidate pages, and prefers primary or otherwise authoritative sources.
3. Uses a separate web-backed verification request, with the complete immutable body for context, to check that every exact source supports the adjacent claim. A separate audit also checks every proposed zero-link decision.
4. Locally inserts only `<a href="...">` and `</a>` around one exact, unique substring in an existing text node.
5. Rejects nested, overlapping, ambiguous, non-HTTPS, unopened, generic, search-result, or tracking URLs. After one correction attempt, an unsafe individual proposal is skipped without discarding other valid proposals.
6. Proves that removing only the new wrappers restores the original body byte for byte. Structurally invalid model responses fail the run before the enriched JSON or Webflow write.
7. Reads every staged write back before any configured publish step and reads every live update or publish back too. The CMS verification checks the body as well as image count, gallery order, ALT text and hosted accessibility. A mismatch stops the run and is not recorded as a successful sync.

There is no fixed one-link or two-link limit. The result is the minimum useful number for the post, which can be zero, one, two, or more.

The evidence-link stage does not change the pipeline's existing `WEBFLOW_PUBLISH` setting. It only adds and verifies the body links; publication remains controlled by the existing configuration.

## ALT Text

Every image should leave the enrichment step with ALT text.

For an OpenAI-generated fallback, `gpt-5.6-sol` inspects the sole raw PNG and writes the final ALT text from the post's central claim plus the people, action, setting, and metaphor actually visible in that PNG. The pipeline stores this reviewed value in the generated-image manifest and sends it with `main-image` to Webflow; it does not rely on the pre-render concept description.

The pipeline tries, in order:

1. OpenAI vision, using the actual image URL and the LinkedIn post context.
2. Any explicit image description already written in the post.
3. A text-only OpenAI fallback.
4. A simple local fallback.

The ALT prompt includes both:

- the image source URL;
- the post context.

This helps OpenAI describe the specific image instead of writing generic ALT text about the whole post.

## Webflow

The Webflow script is now tuned to the exact Blog Posts collection schema.

It fills these fields:

| Webflow field | Value sent by the pipeline |
| --- | --- |
| `name` | Generated headline. |
| `post-summary` | Generated description. |
| `post-body` | LinkedIn post content as rich text HTML. |
| `post-images` | Ordered source-image objects only. Omitted for a generated fallback. |
| `published-date` | LinkedIn publish date. |
| `linkedin-post-link` | Original LinkedIn post URL. |
| `author` | The configured Webflow author item. |
| `main-image` | First source image, or the generated fallback when no source image exists. |
| `thumbnail-image` | First source image only. Omitted for a generated fallback. |
| `category` | Optional, if present in the post data. |
| `tags` | Optional, if present in the post data. |
| `month` | Optional, if present in the post data. |
| `featured` | Optional, if present in the post data. |

The pipeline does not send `slug` at all. Webflow is left to handle that field.

To avoid duplicates, the pipeline checks live Webflow items by LinkedIn URL before enrichment starts.

If Webflow already has a live item with the same LinkedIn URL and no unfinished verification checkpoint, the pipeline stops before writing local output files, uploading assets, calling OpenAI, or updating Webflow. Live Webflow data determines whether the post exists; a saved verification checkpoint records work from an interrupted run that still needs checking.

Before changing an existing item, after receiving a newly created item's ID, and before publishing, the pipeline saves a `verification_pending` checkpoint inside `data/webflow_items.json`. It records the expected body, image fields, image fingerprints, item ID and publication intent. Success replaces that checkpoint with the normal completed item state.

On retry, `pipeline.main` processes every pending checkpoint before fetching the latest LinkedIn post. An older item still gets verified even if a newer post has appeared or the older one has left the 48-hour lookup window. An already-published item can finish through read-back alone. A verified staged item can finish its intended publish. Confirmed content mismatches are repaired on the same item from the saved fields, then checked again. Network, authentication or image-download failures retain the checkpoint without triggering a needless content rewrite. Recovery uses saved content and hosted images without another enrichment or image-generation request. This prevents a successful publish followed by a failed read-back from being mistaken for a completed sync on the next run.

`FORCE_WEBFLOW_SYNC=true` is the intentional override for maintenance runs where you really do want to enrich and sync a matching live Webflow item again.

## GitHub Action Schedule

The workflow runs once per day from a single GitHub cron schedule.

The file is:

```text
.github/workflows/webflow_cms_pipeline.yml
```

GitHub schedules use UTC. The current schedule is `17 0 * * *`, so it starts at 00:17 UTC. There is no extra timezone check or hidden schedule gate inside the workflow.

You can also start the workflow manually from GitHub Actions.

The mutating production job is guarded to `main`. Selecting a feature branch manually cannot publish Webflow or push that feature branch into `main`.

The workflow first checks that its required GitHub secrets and site variable are present. For an image-less post, it then runs `pipeline.prepare_image`, commits the generated PNG and manifest for recovery, and runs `pipeline.main`. The image upload uses local bytes from the runner; the commit is a backup of the reviewed result and is not image hosting.

After a successful run, the workflow commits updates under:

```text
data/
images/
```

If `pipeline.main` fails, a separate recovery step commits changes only in `data/webflow_assets.json` and `data/webflow_items.json`, when those files exist. This preserves upload progress and unfinished CMS verification. The original run remains failed; raw posts, enriched posts, pipeline success state and image changes are left out of that recovery commit. Rebase conflicts and rejected pushes are failures, including recovery-persistence failures; inspect and resolve them before retrying. A failed run also retains the two available recovery files as a seven-day `webflow-recovery-state` Actions artifact, for recovery if Git persistence failed.

GitHub's normal `GITHUB_TOKEN` authenticates the checkout and the bot's pushes using the workflow's existing `contents: write` permission. A private repository does not require a new personal access token for those steps. Ensure branch rules still permit the existing bot writes. Private-repository runs on standard GitHub-hosted runners consume the account's included Actions allowance and any configured paid budget; check the account's current usage before changing visibility. See [GitHub Actions authentication](https://docs.github.com/en/actions/concepts/security/github_token) and [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Switching the GitHub repository to private

Change visibility only after the image-upload implementation has passed its tests and a controlled live check. The code no longer uses public GitHub image URLs, but older Webflow posts must be audited separately.

1. Update the GitHub `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` secret and add the `WEBFLOW_SITE_ID` repository variable. Keep CMS and Assets read/write permissions on the token; site settings permission is not needed.
2. Run `python -m unittest discover -s tests -v`. The suite includes source and generated image uploads, reuse, failure handling and workflow cache recovery without real API calls.
3. Audit every staged and live Webflow item, including `main-image`, `thumbnail-image`, `post-images` and embedded body images, for this repository's GitHub image URLs. Migrate any remaining references to verified Webflow-hosted assets before making the repository private. Preserve body words, image order, ALT text and existing publish state.
4. Use a dedicated Webflow test item or collection for the controlled image-upload and CMS read-back check. Check a source-image gallery and a reviewed generated fallback. Confirm public image loading and a repeated run's cached reuse. `WEBFLOW_PUBLISH=false` still writes drafts and uploads public assets, so it is not a harmless preview setting.
5. Merge and deploy the tested changes through the repository's normal approval process. Check authenticated Git access, Actions allowance and branch permissions. Then change the repository's visibility under **Settings → General → Danger Zone**, following [GitHub's visibility instructions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility).
6. Verify a GitHub production run after the switch, including asset uploads or reuse, CMS read-back, configured publication, bot output commits and public blog image loading. A run that neither processes a new post nor recovers a pending item proves the no-post path only.

Run the read-only CMS audit with the local environment active:

~~~bash
python -m pipeline.audit_image_hosts --output /tmp/private-repo-image-host-audit.json
~~~

It reads every staged and live CMS item, checks native and embedded image references, and writes a local report with counts and flagged item/field/URL details. It never writes CMS items or publishes. Exit code `0` means the complete audit found no repository-dependent images; a nonzero exit means dependencies or an audit error need attention. Ordinary links to the repository are reported separately because authorised readers may still follow them after the visibility change. This audit proves image-reference coverage; use the controlled integration check to prove image uploads and CMS writes.

Existing images already hosted on Webflow remain accessible when GitHub becomes private. Repository privacy does not make Webflow Assets private. Keep uploaded content suitable for the public blog, and do not put credentials in image files or the tracked cache.

## Project Files

| Path | Purpose |
| --- | --- |
| `pipeline/main.py` | The main pipeline flow. |
| `pipeline/linkedin.py` | Fetches the latest LinkedIn post from the last 48 hours. |
| `pipeline/enrichment.py` | Creates headline, summary, and ALT text. |
| `pipeline/linking.py` | Researches, verifies, validates, and inserts authoritative evidence links without changing body wording. |
| `pipeline/image_generation.py` | Plans, generates once, reviews, reuses, and attaches a generated fallback PNG. |
| `pipeline/image_processing.py` | Validates the raw result and prepares an exact-16:9 PNG under 800,000 bytes. |
| `pipeline/image_references.py` | Validates and resolves the eleven bundled style references. |
| `pipeline/image_assets.py` | Resolves source and generated files and prepares Webflow-hosted image URLs before enrichment. |
| `pipeline/audit_image_hosts.py` | Read-only audit of all staged/live Webflow image references for repository dependencies. |
| `pipeline/prepare_image.py` | Runs the pre-Webflow missing-image preparation stage. |
| `pipeline/seo_preview.py` | Safely previews SEO metadata using a saved post and OpenAI only. |
| `pipeline/webflow.py` | Builds the exact Webflow payload and syncs the CMS item. |
| `pipeline/config.py` | Environment variables and defaults. |
| `config/prompts.json` | OpenAI prompts. |
| `images/` | Date-named LinkedIn source images; only direct files here enter the normal image pipeline. |
| `images/generated/` | OpenAI-generated PNG fallbacks, kept separate from source images. |
| `assets/blog-main-image-style/` | Eleven bundled PNG style references used by every fallback generation. |
| `data/generated_main_images.json` | Manifest for generated PNG checksums, provenance, prompt, review, dimensions, bytes, and ALT text. |
| `data/webflow_assets.json` | Reusable per-site Webflow asset IDs, content fingerprints and verified hosted URLs. |
| `data/` | Saved pipeline state and latest generated JSON files. |
| `tests/` | Tests for the pipeline behavior. |
| `webflow_schema.json` | Reference snapshot of the Webflow Blog Posts collection schema. |
| `webflow_schema_item_example.json` | Reference snapshot of a Webflow Blog Posts item. |

## Saved Data

The script writes these files:

| File | What it contains |
| --- | --- |
| `data/last_linkedin_post.json` | The latest raw LinkedIn post found. |
| `data/last_linkedin_post.enriched.json` | The post after headline, summary, ALT text, image attachment, and verified evidence links are added. |
| `data/webflow_items.json` | Webflow item IDs, completed sync state and unfinished verification checkpoints. |
| `data/pipeline_state.json` | The latest run status. |
| `data/webflow_assets.json` | Asset creation, pending upload and verified upload checkpoints, saved independently of the final post-sync outcome. |

## Webflow Maintenance Override

Use this only when you intentionally want to bypass the normal live-item check.

| Flag | What it does |
| --- | --- |
| `FORCE_WEBFLOW_SYNC=true` | Enriches and syncs the post even when Webflow already has a live item with the same LinkedIn URL. |

## Prompt Limits

The headline and description limits are stored in:

```text
pipeline/enrichment.py
```

Current values:

```text
HEADLINE_MIN = 45
HEADLINE_TARGET_MIN = 48
HEADLINE_TARGET_MAX = 58
HEADLINE_MAX = 60
DESCRIPTION_TARGET_MIN = 145
DESCRIPTION_TARGET_MAX = 155
DESCRIPTION_MAX = 160
ALT_MAX = 180
```

The pipeline asks OpenAI to regenerate metadata that breaks selected machine-checkable publishing rules, up to two attempts. It fails instead of silently cutting an overlong title or description into a fragment.

If the supplied material cannot support accurate metadata, the model can return the pipeline's insufficient-source signal. The run then stops with a request for more source content instead of publishing an invented hook.

The runtime processes one post at a time and keeps the repository's internal `headline` and `description` keys. Webflow maps `headline` to the visible title; this adapter changes only the data envelope, not the SEO policy.
