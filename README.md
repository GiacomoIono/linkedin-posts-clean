# LinkedIn Posts Clean

This project takes your latest LinkedIn post and turns it into a Webflow blog post.

In plain English, the pipeline does this:

1. Looks for your newest LinkedIn post from the last 48 hours.
2. Turns the post text into simple blog-post HTML.
3. Finds matching images in the `images/` folder.
4. If LinkedIn reports no image and no matching source file exists, uses OpenAI to create one 16:9 JPEG fallback.
5. Uses OpenAI to create the headline, summary, and missing image ALT text.
6. Sends the post to the Webflow Blog Posts collection.
7. Skips X/Twitter unless you explicitly turn that part on.
8. Saves a record of what happened in the `data/` folder.

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

Cloning this public repository does not require authentication, but pushing changes does. GitHub account passwords cannot be used as Git passwords. GitHub CLI will configure secure HTTPS authentication.

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

Never put an OpenAI, LinkedIn, Webflow, or X API token into Git's username, email, or remote URL.

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
- `python-dotenv`
- `requests`

Do not use `sudo pip install`. Do not install these packages globally. Do not run `npm install`; this repository has no Node.js packages.

Whenever `requirements.txt` changes after a future `git pull`, reactivate `.venv` and run:

~~~bash
python -m pip install -r requirements.txt
~~~

### Part 7: restore or create the local environment variables

The local `.env` file contains secrets and is deliberately excluded from Git. It will not arrive with `git clone`.

1. Create the file in the repository root, next to `README.md` and `requirements.txt`.

~~~bash
touch .env
code .env
~~~

If the `code` command is unavailable, open the repository in Visual Studio Code and create a file named exactly `.env`.

2. Paste this template:

~~~dotenv
LINKEDIN_ACCESS_TOKEN=

OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-nano
OPENAI_IMAGE_MODEL=gpt-image-2

WEBFLOW_API_TOKEN=
WEBFLOW_COLLECTION_ID=63250855178122098387d7ef
WEBFLOW_PUBLISH=true

RUN_X_PIPELINE=false
X_ACCESS_TOKEN=
REQUIRE_X_POSTING=false

FORCE_WEBFLOW_SYNC=false
FORCE_ENRICH=false
FORCE_TWEETIFY=false
FORCE_X_POST=false
~~~

3. Add the real values after each required equals sign:

| Local variable | Required? | Where the value comes from |
| --- | --- | --- |
| `LINKEDIN_ACCESS_TOKEN` | Yes | Secure copy of the old `.env` value, or a newly authorised token from the LinkedIn developer application. |
| `OPENAI_API_KEY` | Yes | Secure copy of the old value, or a new key from the same OpenAI API project. Existing key values usually cannot be revealed again after creation. |
| `OPENAI_MODEL` | Yes | Keep `gpt-5-nano` unless the pipeline is intentionally changed. |
| `OPENAI_IMAGE_MODEL` | Yes | Keep `gpt-image-2`. It is used only for a missing-image fallback. |
| `WEBFLOW_API_TOKEN` | Yes | Secure copy of the old token, or a replacement Webflow token with access to read and write the Blog Posts collection. |
| `WEBFLOW_COLLECTION_ID` | Yes | Keep the existing ID shown in the template. |
| `WEBFLOW_PUBLISH` | Yes | Keep `true` for the normal production behaviour. Read the live-run warning below before running locally. |
| `RUN_X_PIPELINE` | Yes | Keep `false` unless X posting is intentionally re-enabled. |
| `X_ACCESS_TOKEN` | No while X is disabled | Leave blank while `RUN_X_PIPELINE=false`. |
| `REQUIRE_X_POSTING` | Yes | Keep `false` while X is optional or disabled. |
| `FORCE_*` variables | Yes | Keep all four `false` during normal use. |

Do not add spaces around the equals sign. Do not wrap the tokens in quotation marks unless a value genuinely contains spaces.

#### Local secrets and GitHub Actions secrets are separate

The scheduled GitHub Action currently reads:

- `LINKEDIN_ACCESS_TOKEN` from a GitHub Actions secret.
- `OPENAI_API_KEY` from a GitHub Actions secret.
- `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` from a GitHub Actions secret or repository variable.
- `X_ACCESS_TOKEN` from an optional GitHub Actions secret.

Those remote values remain in GitHub when you change MacBook. You do not need to recreate them merely because you cloned the repository elsewhere. GitHub intentionally hides saved secret values; it will not let you copy them back out for the local `.env` file.

If the old `.env` is unavailable, create or rotate the required credentials in the relevant services. Do not weaken security by trying to extract hidden GitHub Actions secrets.

4. Save `.env` and verify that Git ignores both local-only items:

~~~bash
git check-ignore -v .env
git check-ignore -v .venv/
git status --short
~~~

The first two commands should show an ignore rule. The final command should not list `.env` or `.venv` and should normally print nothing.

5. Confirm that the application can see the required settings without printing the secret values:

~~~bash
python -c "from pipeline.config import load_config; c=load_config(); print('LinkedIn configured:', bool(c.linkedin_access_token)); print('OpenAI configured:', bool(c.openai_api_key)); print('Webflow configured:', bool(c.webflow_api_token)); print('X pipeline enabled:', c.run_x_pipeline)"
~~~

Expected results for the normal configuration:

~~~text
LinkedIn configured: True
OpenAI configured: True
Webflow configured: True
X pipeline enabled: False
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

These tests are the correct first verification because every OpenAI, LinkedIn, and Webflow call is mocked. They make no paid API calls and do not write to Webflow or X.

For an optional read-only LinkedIn check:

1. Open the repository's [Actions tab](https://github.com/GiacomoIono/linkedin-posts-clean/actions).
2. Select `Validate LinkedIn Fetch`.
3. Select `Run workflow`.
4. Keep the branch set to `main` and start it.
5. Open the run and confirm that all steps are green.

That workflow runs the tests and calls the LinkedIn API read-only. It does not run the Webflow publishing pipeline.

The same manual form has a `live_image_smoke` option. Leave it off for normal validation. If you deliberately enable it, the workflow uses the existing `OPENAI_API_KEY` secret to create one paid JPEG preview, saves it as a one-day GitHub Actions artifact, and never commits it or sends it to Webflow.

### Part 9: understand the live-run warning

Do not run the following command merely to check whether installation worked:

~~~bash
python -m pipeline.main
~~~

It is a live end-to-end command. With valid credentials, it can:

- read recent LinkedIn activity;
- call the OpenAI API and incur API usage;
- require a generated fallback JPEG to be committed before Webflow can fetch it when the post has no source image;
- create or update a Webflow CMS item;
- publish that item when `WEBFLOW_PUBLISH=true`;
- write output files under `data/`;
- post to X if `RUN_X_PIPELINE=true`.

Important: `WEBFLOW_PUBLISH=false` is not a complete dry-run mode. It prevents the final publish step, but the pipeline can still create or update a Webflow draft item.

The normal scheduled GitHub Action already runs the production pipeline. A local live run is optional and should be used only when you intentionally want to process a real LinkedIn post.

### Part 10: perform the first intentional live run

Only do this when all of the following are true:

- There is a real LinkedIn post inside the rolling 48-hour window.
- The local `.env` values are configured.
- `RUN_X_PIPELINE=false` unless X posting is deliberately wanted.
- All `FORCE_*` flags are `false` unless a maintenance override is deliberately required.
- Any matching image has the correct date-based filename.
- Any new image is already committed and available on GitHub's `main` branch.

That last point is essential: image URLs are built from the repository's raw `main` branch. A brand-new image that exists only on the laptop or only on an unmerged branch cannot be fetched by Webflow or OpenAI.

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
- If no qualifying LinkedIn post exists within 48 hours, it prints `No recent LinkedIn posts found` and exits with code `2`. The scheduled GitHub Action deliberately treats that as “nothing to do.”
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
- the Webflow token can read and write the configured Blog Posts collection.

Do not print the full token in Terminal screenshots or support messages.

#### A commit is blocked because a file exceeds 90 MB

The repository hook is protecting GitHub from a large binary. Do not bypass it casually. Remove the large file from the staged change or use an appropriate external storage strategy.

#### An image exists locally but is missing from Webflow

Check all three conditions:

1. Its filename begins with the LinkedIn publication date in `YYYY-MM-DD` format.
2. Its extension is `.jpg`, `.jpeg`, `.png`, or `.webp`.
3. The file is committed and visible in the `images/` folder on GitHub's `main` branch before the pipeline runs.

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
- [ ] The configuration check reports the three required services as configured.
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

## The Important Settings

Most days, these are the only settings you need to care about:

| Setting | What it does |
| --- | --- |
| `LINKEDIN_ACCESS_TOKEN` | Lets the script read your recent LinkedIn activity. |
| `OPENAI_API_KEY` | Lets the script write metadata, ALT text, and a missing-image fallback. |
| `OPENAI_IMAGE_MODEL` | Selects the image model; the default is `gpt-image-2`. |
| `WEBFLOW_API_TOKEN` | Lets the script create, update, and publish Webflow posts. |
| `WEBFLOW_PUBLISH` | When `true`, Webflow items are published after they are written. |
| `RUN_X_PIPELINE` | When `false`, the X pipeline is skipped completely. This is the default. |
| `X_ACCESS_TOKEN` | Needed only if `RUN_X_PIPELINE=true`. |

`WEBFLOW_READ_AND_WRITE_BLOG_POSTS` can also be used instead of `WEBFLOW_API_TOKEN`.

## LinkedIn Window

The LinkedIn scraper looks back exactly 48 hours from the time the script runs.

That means it is a rolling time window, not "today and yesterday" as calendar days. For example, if the script runs at 04:00 on June 3, it searches back to 04:00 on June 1.

LinkedIn is queried in pages of 50 changelog records. The scraper follows every page until it reaches the end of the 48-hour window, and it stops if a page contains only older records. A temporary LinkedIn `500` response is retried twice with a short backoff before the run fails.

If no LinkedIn post is found in that window, the script exits cleanly with code `2`. The GitHub Action treats that as "nothing to do", not as a failure.

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

Important: the current pipeline does not download media directly from LinkedIn. In this project, a "source image" means a date-matched file already present at the top level of `images/`. The pipeline also records LinkedIn's own image signal. If LinkedIn reports an image but the matching local source file is missing, the workflow stops instead of replacing that image with an AI fallback.

When there are multiple images, the number decides the order. `_1` is first, `_2` is second, and so on. When there is only one image, the filename can just be the date.

The pipeline sends:

- all images to Webflow's `post-images` field, in the right order;
- the first image to `main-image`;
- the first image to `thumbnail-image`;
- an `alt` value for every image.

Important: image URLs are built from the GitHub `main` branch. So if you run the pipeline locally with brand-new local images, Webflow can only fetch them after those images exist on GitHub.

### OpenAI fallback for a post without a source image

When LinkedIn reports that the post has no image and there is no matching source file, the production workflow:

1. Checks again that Webflow does not already have the LinkedIn URL, avoiding an unnecessary paid image request.
2. Asks `gpt-image-2` for exactly one high-quality `1536 x 864` JPEG.
3. Uses the post text and the noir, graphic-novel editorial prompt in `config/prompts.json`.
4. Validates that the result is JPEG, exactly 16:9, and no larger than Webflow's 4 MB image limit.
5. Saves it in the usual top-level `images/` folder as `<post-date>.jpeg`, matching the existing image convention.
6. Reuses that file after a retry instead of paying to generate it again.
7. Commits and pushes the JPEG before the CMS step, then gives Webflow a URL pinned to that exact Git commit.

Generated fallback images use the same folder and date naming convention as the other image files, for example `images/2026-08-25.jpeg`. The pipeline records their LinkedIn URL and checksum in `data/generated_main_images.json`. That registry keeps a generated JPEG out of the normal source-image list after the workflow refetches the post.

Because the filename is date-only, two image-less LinkedIn posts published on the same UTC date cannot safely have different generated images. The pipeline detects that collision and stops before making another paid image request or overwriting the first file.

For a generated fallback, Webflow receives:

- the JPEG in `main-image` only;
- no value in `post-images`;
- no value in `thumbnail-image`.

If a LinkedIn image is missing locally, or if image generation, validation, public-URL verification, or the pre-Webflow Git push fails, the workflow stops. It does not publish an image-less replacement post.

## ALT Text

Every image should leave the enrichment step with ALT text.

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

If Webflow already has a live item with the same LinkedIn URL, the pipeline stops before writing local output files, calling OpenAI, or updating Webflow. Local files in `data/` are not used to decide whether a post already exists.

`FORCE_WEBFLOW_SYNC=true` is the intentional override for maintenance runs where you really do want to replace an existing live Webflow item.

## X Posting

The X pipeline is disabled by default.

```bash
RUN_X_PIPELINE=false
```

When it is `false`, the script does not generate a tweet, does not upload images to X, and does not call the X API.

If you set:

```bash
RUN_X_PIPELINE=true
```

then the script tries to:

1. Generate a tweet from the enriched LinkedIn post.
2. Select up to four images.
3. Upload those images to X.
4. Add ALT text metadata.
5. Publish the post.
6. Save the result in `data/posted_tweets.json`.

If X fails, the Webflow pipeline still succeeds unless `REQUIRE_X_POSTING=true`.

## GitHub Action Schedule

The workflow runs once per day from a single GitHub cron schedule.

The file is:

```text
.github/workflows/webflow_cms_pipeline.yml
```

GitHub schedules use UTC. The current schedule is `17 0 * * *`, so it starts at 00:17 UTC. There is no extra timezone check or hidden schedule gate inside the workflow.

You can also start the workflow manually from GitHub Actions.

The mutating production job is guarded to `main`. Selecting a feature branch manually cannot publish Webflow or push that feature branch into `main`.

For an image-less post, the workflow first runs `pipeline.prepare_image`, commits the generated JPEG, and only then runs `pipeline.main`. This order is required because Webflow must be able to fetch the public image URL during the CMS write.

After a successful run, the workflow commits updates under:

```text
data/
images/
```

## Project Files

| Path | Purpose |
| --- | --- |
| `pipeline/main.py` | The main pipeline flow. |
| `pipeline/linkedin.py` | Fetches the latest LinkedIn post from the last 48 hours. |
| `pipeline/enrichment.py` | Creates headline, summary, and ALT text. |
| `pipeline/image_generation.py` | Creates, validates, reuses, and attaches a generated fallback JPEG. |
| `pipeline/prepare_image.py` | Runs the pre-Webflow missing-image preparation stage. |
| `pipeline/webflow.py` | Builds the exact Webflow payload and syncs the CMS item. |
| `pipeline/x_posting.py` | Optional X/Twitter generation and posting. |
| `pipeline/config.py` | Environment variables and defaults. |
| `config/prompts.json` | OpenAI prompts. |
| `images/` | Date-named source images and OpenAI JPEG fallbacks. |
| `data/generated_main_images.json` | Registry that keeps generated JPEGs separate from source-image fields. |
| `data/` | Saved pipeline state and latest generated JSON files. |
| `tests/` | Tests for the pipeline behavior. |
| `webflow_schema.json` | Reference copy of the Webflow collection schema. |
| `webflow_schema_item_example.json` | Reference copy of a real Webflow item. |

## Saved Data

The script writes these files:

| File | What it contains |
| --- | --- |
| `data/last_linkedin_post.json` | The latest raw LinkedIn post found. |
| `data/last_linkedin_post.enriched.json` | The post after headline, summary, and ALT text are added. |
| `data/webflow_items.json` | Webflow item IDs and sync state. |
| `data/tweet.json` | The generated X draft, only when X is enabled. |
| `data/posted_tweets.json` | A ledger used to avoid posting the same thing to X twice. |
| `data/pipeline_state.json` | The latest run status. |

## Useful Force Flags

Use these only when you want to override the normal "skip if already done" behavior.

| Flag | What it does |
| --- | --- |
| `FORCE_WEBFLOW_SYNC=true` | Writes to Webflow even if the saved state says it is already current. |
| `FORCE_ENRICH=true` | Regenerates headline, summary, and ALT text. |
| `FORCE_TWEETIFY=true` | Regenerates the X draft. |
| `FORCE_X_POST=true` | Ignores the X posting ledger and posts again. |

## Prompt Limits

The headline and description limits are stored in:

```text
pipeline/enrichment.py
```

Current values:

```text
HEADLINE_MAX = 70
DESCRIPTION_MAX = 160
ALT_MAX = 180
```
