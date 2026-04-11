#!/bin/bash
set -e
cd /home/kb/source/repos/OceanICU/oceanicu_3d/analyses

if [ ! -d NS ] && [ ! -d ENA4 ] && [ ! -d ENA8 ] && [ ! -d AMM7 ]; then
  echo "Already migrated — nothing to do."
  exit 0
fi

mkdir -p areas scenarios validations

for area in AMM7 EAN4 ENA4 ENA8 NS; do
  [ -d "$area" ] || continue
  mkdir -p areas/$area/scenarios areas/$area/validations
  for exp in "$area"/*/; do
    [ -d "$exp" ] && mv "$exp" areas/$area/validations/
  done
  [ -f "$area/area.md" ] && mv "$area/area.md" areas/$area/area.md
  rmdir "$area" 2>/dev/null || true
done

if [ -d scenarios ] && ls scenarios/ | grep -q .; then
  mv scenarios/* areas/NS/scenarios/
fi
rmdir scenarios 2>/dev/null || true

echo "Migration complete."
find . -maxdepth 3 -type d | sort
