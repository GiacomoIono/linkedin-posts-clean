# run_pipeline.py
# Executes the LinkedIn to CMS pipeline, then optionally adapts/posts to X.

import sys
import subprocess
import time
from pathlib import Path

# --- Configuration ---
REPO_ROOT = Path(__file__).resolve().parent
NO_POSTS_FOUND_EXIT_CODE = 2


def run_script(script_path: Path, allowed_exit_codes=(0,), required=True):
    """Execute a Python script and optionally allow the pipeline to continue after errors."""
    if not script_path.exists():
        print(f"❌ Script not found: {script_path.name}", file=sys.stderr)
        if required:
            sys.exit(1)
        return 1

    print(f"\n--- Running {script_path.name} ---")

    # We use sys.executable to ensure it runs with the same Python environment
    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode not in allowed_exit_codes:
        print(f"❌ {script_path.name} failed with exit code {result.returncode}", file=sys.stderr)
        if required:
            sys.exit(1)
        print(f"⚠️ Continuing because {script_path.name} is optional for the Webflow CMS JSON.")
        return result.returncode

    if result.returncode == 0:
        print(f"✅ {script_path.name} finished successfully.")

    return result.returncode


def pause_between_steps():
    print("⏳ Pausing for 10 seconds...")
    time.sleep(10)


def main():
    """Run the full pipeline."""
    print("🚀 Starting the LinkedIn to CMS pipeline, with optional X posting...")

    # Define the paths to your scripts in the correct order
    fetch_script = REPO_ROOT / "fetch_posts.py"
    enrich_script = REPO_ROOT / "enrich_post.py"
    tweetify_script = REPO_ROOT / "Twitter" / "tweetify_post.py"
    post_tweet_script = REPO_ROOT / "Twitter" / "post_tweet.py"

    # Required path: this keeps the public Webflow JSON up to date.
    fetch_status = run_script(fetch_script, allowed_exit_codes=(0, NO_POSTS_FOUND_EXIT_CODE), required=True)
    if fetch_status == NO_POSTS_FOUND_EXIT_CODE:
        print("\nNo recent LinkedIn posts found. Keeping the existing CMS JSON untouched.")
        print("Pipeline stopped before enrichment, tweet generation, and X posting.")
        return

    pause_between_steps()

    run_script(enrich_script, required=True)

    # Optional path: X/Twitter errors should be visible in logs but must not block Webflow JSON updates.
    pause_between_steps()
    tweetify_status = run_script(tweetify_script, required=False)

    if tweetify_status == 0:
        pause_between_steps()
        post_tweet_status = run_script(post_tweet_script, required=False)
        if post_tweet_status != 0:
            print("\n⚠️ X posting failed, but the CMS JSON was generated successfully.")
    else:
        print("\n⚠️ Tweet generation failed, so X posting was skipped. The CMS JSON was generated successfully.")

    print("\n🎉 Required pipeline completed successfully. Webflow CMS JSON is ready to commit/publish.")


if __name__ == "__main__":
    main()
