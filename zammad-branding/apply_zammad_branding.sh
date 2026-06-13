#!/bin/bash
set -e

COMPOSE_DIR="/opt/zammad-docker-compose"
CSS_SOURCE="/opt/ai-support-agent/zammad-branding/national_finance.css"
LOGO_SOURCE="/opt/ai-support-agent/zammad-branding/assets/tct-logo.png"
LOG_FILE="/var/log/zammad-branding.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "======================================================"
echo "Applying National Finance CSS-only branding to Zammad"
echo "Started: $(date)"
echo "======================================================"

cd "$COMPOSE_DIR"

echo "[1/5] Checking containers..."
docker compose ps

echo "[2/5] Copying CSS into app containers..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Copying CSS to $svc"
  docker compose cp "$CSS_SOURCE" "$svc:/tmp/national_finance.css" || true
done

echo "[2B/5] Copying TCT logo into public assets..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Copying TCT logo to $svc"
  docker compose cp "$LOGO_SOURCE" "$svc:/opt/zammad/public/assets/tct-logo.png" || true
done

echo "[3/5] Appending custom CSS to compiled CSS assets..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Patching CSS in $svc"

  docker compose exec -T -u 0 "$svc" bash -lc '
    if [ -d /opt/zammad/public/assets ]; then
      for css in /opt/zammad/public/assets/*.css; do
        if [ -f "$css" ]; then
          sed -i "/\/\* NATIONAL FINANCE BRANDING START \*\//,/\/\* NATIONAL FINANCE BRANDING END \*\//d" "$css" || true
          {
            echo ""
            echo "/* NATIONAL FINANCE BRANDING START */"
            cat /tmp/national_finance.css
            echo "/* NATIONAL FINANCE BRANDING END */"
          } >> "$css"
        fi
      done
    fi
  ' || true
done

echo "[4/5] Removing stale compressed CSS only..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Removing CSS gzip assets in $svc"

  docker compose exec -T -u 0 "$svc" bash -lc '
    if [ -d /opt/zammad/public/assets ]; then
      rm -f /opt/zammad/public/assets/*.css.gz || true
    fi
  ' || true
done

echo "[5/5] Restarting Zammad app containers..."
docker compose restart zammad-nginx zammad-railsserver zammad-websocket zammad-scheduler

echo "======================================================"
echo "Branding applied successfully"
echo "Finished: $(date)"
echo "======================================================"