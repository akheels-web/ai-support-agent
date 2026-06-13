#!/bin/bash
set -e

COMPOSE_DIR="/opt/zammad-docker-compose"
CSS_SOURCE="/opt/ai-support-agent/zammad-branding/national_finance.css"
CSS_TARGET="/opt/zammad/app/assets/stylesheets/custom/national_finance.css"
LOG_FILE="/var/log/zammad-branding.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "======================================================"
echo "Applying National Finance branding to Zammad"
echo "Started: $(date)"
echo "======================================================"

cd "$COMPOSE_DIR"

echo "[1/7] Checking containers..."
docker compose ps

echo "[2/7] Creating custom CSS directory..."
docker compose exec -T zammad-railsserver bash -lc "mkdir -p /opt/zammad/app/assets/stylesheets/custom"

echo "[3/7] Copying custom CSS..."
docker compose cp "$CSS_SOURCE" zammad-railsserver:"$CSS_TARGET"

echo "[4/7] Setting CSS permissions..."
docker compose exec -T -u 0 zammad-railsserver bash -lc "chmod 644 $CSS_TARGET || true"

echo "[5/7] Removing Powered by Zammad from known templates..."
docker compose exec -T zammad-railsserver bash -lc '
FILES="
/opt/zammad/app/assets/javascripts/app/views/login.jst.eco
/opt/zammad/app/assets/javascripts/app/views/password/reset.jst.eco
/opt/zammad/app/assets/javascripts/app/views/password/reset_sent.jst.eco
/opt/zammad/app/assets/javascripts/app/views/password/reset_failed.jst.eco
/opt/zammad/app/assets/javascripts/app/views/password/reset_change.jst.eco
/opt/zammad/app/assets/javascripts/app/views/admin_password_auth/request.jst.eco
/opt/zammad/app/assets/javascripts/app/views/admin_password_auth/request_sent.jst.eco
/opt/zammad/app/assets/javascripts/app/views/signup.jst.eco
/opt/zammad/app/assets/javascripts/app/views/signup/verify.jst.eco
"

for file in $FILES; do
  if [ -f "$file" ]; then
    echo "Patching $file"

    cp "$file" "$file.nf-backup" 2>/dev/null || true

    perl -0777 -pi -e "s!<div class=\"poweredBy\">.*?</div>!!sg" "$file"
    perl -0777 -pi -e "s!<div class=\"powered-by\">.*?</div>!!sg" "$file"
    perl -0777 -pi -e "s!Powered by Zammad!!sg" "$file"
  fi
done
'

echo "[6/7] Precompiling assets..."
docker compose exec -T -u 0 zammad-railsserver bash -lc "cd /opt/zammad && bundle exec rake assets:precompile --trace"

echo "[7/7] Restarting Zammad containers..."
docker compose restart

echo "======================================================"
echo "Branding applied successfully"
echo "Finished: $(date)"
echo "======================================================"
