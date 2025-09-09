# run_pipeline.py
# Executes the entire LinkedIn to X pipeline in the correct order with delays.

import sys
import subprocess
import time
from pathlib import Path

# --- Configuration ---
REPO_ROOT = Path(__file__).resolve().parent

def run_script(script_path: Path):
    """Executes a Python script and checks for errors."""
    if not script_path.exists():
        print(f"❌ Script not found: {script_path.name}", file=sys.stderr)
        sys.exit(1)
        
    print(f"\n--- Running {script_path.name} ---")
    
    # We use sys.executable to ensure it runs with the same Python environment
    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
        
    if result.returncode != 0:
        print(f"❌ {script_path.name} failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(1)
        
    print(f"✅ {script_path.name} finished successfully.")

def main():
    """Run the full pipeline."""
    print("🚀 Starting the full LinkedIn to X pipeline...")
    
    # Define the paths to your scripts in the correct order
    fetch_script = REPO_ROOT / "fetch_posts.py"
    enrich_script = REPO_ROOT / "enrich_post.py"
    tweetify_script = REPO_ROOT / "Twitter" / "tweetify_post.py"
    post_tweet_script = REPO_ROOT / "Twitter" / "post_tweet.py"
    
    # Run scripts in sequence with a 10-second pause between each
    run_script(fetch_script)
    print("⏳ Pausing for 10 seconds...")
    time.sleep(10)
    
    run_script(enrich_script)
    print("⏳ Pausing for 10 seconds...")
    time.sleep(10)

    run_script(tweetify_script)
    print("⏳ Pausing for 10 seconds...")
    time.sleep(10)

    run_script(post_tweet_script)
    
    print("\n🎉 Pipeline completed successfully!")

if __name__ == "__main__":
    main()