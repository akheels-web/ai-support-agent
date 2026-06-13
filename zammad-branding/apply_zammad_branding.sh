#!/bin/bash
set -e

COMPOSE_DIR="/opt/zammad-docker-compose"
CSS_SOURCE="/opt/ai-support-agent/zammad-branding/national_finance.css"
LOG_FILE="/var/log/zammad-branding.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "======================================================"
echo "Applying National Finance branding to Zammad"
echo "Started: $(date)"
echo "======================================================"

cd "$COMPOSE_DIR"

echo "[1/6] Checking containers..."
docker compose ps

echo "[2/6] Copying CSS into containers..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Copying CSS to $svc"
  docker compose cp "$CSS_SOURCE" "$svc:/tmp/national_finance.css" || true
done

echo "[3/6] Appending custom CSS to compiled Zammad CSS assets..."
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

echo "[4/6] Removing Powered by Zammad from compiled JS assets..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Patching JS in $svc"

  docker compose exec -T -u 0 "$svc" bash -lc '
    if [ -d /opt/zammad/public/assets ]; then
      for js in /opt/zammad/public/assets/*.js; do
        if [ -f "$js" ]; then
          perl -0777 -pi -e "s!<div class=\\\"poweredBy\\\">.*?</div>!!sg" "$js" || true
          perl -0777 -pi -e "s!<div class=\\\"powered-by\\\">.*?</div>!!sg" "$js" || true
          perl -0777 -pi -e "s!Powered by Zammad!!sg" "$js" || true
          perl -0777 -pi -e "s!poweredBy!!sg" "$js" || true
        fi
      done
    fi
  ' || true
done

echo "[5/6] Removing stale compressed assets so browser uses patched files..."
for svc in zammad-railsserver zammad-nginx zammad-websocket zammad-scheduler; do
  echo "Removing gzip assets in $svc"

  docker compose exec -T -u 0 "$svc" bash -lc '
    if [ -d /opt/zammad/public/assets ]; then
      rm -f /opt/zammad/public/assets/*.css.gz || true
      rm -f /opt/zammad/public/assets/*.js.gz || true
    fi
  ' || true
done

echo "[6/6] Restarting Zammad containers..."
docker compose restart

echo "======================================================"
echo "Branding applied successfully"
echo "Finished: $(date)"
echo "======================================================"