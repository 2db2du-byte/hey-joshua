#!/bin/bash
# Downloads Joshua's bookshelf. Safe to re-run: resumes partial files, skips finished ones.
cd "$(dirname "$(readlink -f "$0")")/zim" || exit 1
LIB=../library.xml
BASE=https://download.kiwix.org/zim
BOOKS=(
  other/zimgit-water_en_2024-08.zim
  other/zimgit-knots_en_2024-08.zim
  other/zimgit-medicine_en_2024-08.zim
  other/zimgit-food-preparation_en_2025-04.zim
  stack_exchange/sustainability.stackexchange.com_en_all_2026-10.zim
  stack_exchange/outdoors.stackexchange.com_en_all_2026-10.zim
  stack_exchange/cooking.stackexchange.com_en_all_2026-10.zim
  wikivoyage/wikivoyage_en_all_nopic_2026-09.zim
  stack_exchange/mechanics.stackexchange.com_en_all_2026-10.zim
  other/appropedia_en_all_maxi_2026-02.zim
  other/zimgit-post-disaster_en_2024-05.zim
  stack_exchange/gardening.stackexchange.com_en_all_2026-10.zim
  other/trueprepper.com_en_all_2026-05.zim
  stack_exchange/diy.stackexchange.com_en_all_2026-10.zim
  other/mdwiki_en_all_maxi_2025-11.zim
  stack_exchange/askubuntu.com_en_all_2026-10.zim
  ifixit/ifixit_en_all_2025-12.zim
  stack_exchange/electronics.stackexchange.com_en_all_2026-10.zim
  wikipedia/wikipedia_en_all_nopic_2026-06.zim
)
n=0
for b in "${BOOKS[@]}"; do
  n=$((n+1)); f=$(basename "$b")
  if [ -f "$f.ok" ]; then echo "[$n/${#BOOKS[@]}] have $f"; continue; fi
  echo "[$n/${#BOOKS[@]}] downloading $f"
  ok=0
  for try in 1 2 3 4 5; do
    # .meta4 = list of mirrors + checksums; aria2 pulls from several at once and verifies the file
    aria2c -q -x 8 -s 8 -j 1 --file-allocation=none --follow-metalink=mem --check-integrity=true \
      --console-log-level=warn --summary-interval=0 -d . "$BASE/$b.meta4" && { ok=1; break; }
    echo "  retry $try"; sleep 20
  done
  [ $ok = 1 ] || { echo "  FAILED $f — will retry next run"; continue; }
  touch "$f.ok"
  kiwix-manage "$LIB" add "$f" && echo "  ✓ shelved $f"
  systemctl --user restart joshua-library 2>/dev/null
done
echo "ALL DONE"
