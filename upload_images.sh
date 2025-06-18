#!/bin/bash
# ----- upload_images.sh -----
cd "$(git rev-parse --show-toplevel)" || exit 1

# Only look at the images/ folder
CHANGES=$(git status --porcelain images | wc -l)

if [ "$CHANGES" -gt 0 ]; then
  git add images/*.jpg 2>/dev/null          # ignore if none match
  TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
  git commit -m "Add/update images $TIMESTAMP"
  git push origin main                      # change 'main' if your branch is different
  echo "✅ Images pushed at $TIMESTAMP"
else
  echo "No new images to push."
fi

