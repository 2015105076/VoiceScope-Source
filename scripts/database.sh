#!/usr/bin/env bash
# VoiceScope local database helper: start, inspect, and migrate MySQL safely.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ -f .env ]]; then
  set -a
  # .env is a local operator-owned configuration file and is ignored by Git.
  source .env
  set +a
fi

mysql_service="${VOICESCOPE_MYSQL_SERVICE:-mysql}"
mysql_host="${VOICESCOPE_MYSQL_HOST:-127.0.0.1}"
mysql_port="${VOICESCOPE_MYSQL_PORT:-3306}"
mysql_user="${VOICESCOPE_MYSQL_USER:-}"
mysql_password="${VOICESCOPE_MYSQL_PASSWORD:-}"
mysql_database="${VOICESCOPE_MYSQL_DATABASE:-voicescope}"

usage() {
  cat <<'EOF'
用法：
  ./scripts/database.sh start             启动 MySQL 并检查服务
  ./scripts/database.sh status            显示 MySQL 和应用数据库状态
  ./scripts/database.sh migrate           预演 SQLite → MySQL 迁移，不写入数据
  ./scripts/database.sh migrate --apply   执行 SQLite → MySQL 迁移

首次配置：复制 .env.example 为 .env，再填写 VOICESCOPE_MYSQL_HOST、USER、PASSWORD、DATABASE。
EOF
}

require_app_config() {
  if [[ -z "$mysql_user" || -z "$mysql_password" || -z "$mysql_database" ]]; then
    echo "缺少 MySQL 应用配置。请复制 .env.example 为 .env 并填写连接信息。" >&2
    exit 2
  fi
}

wait_for_mysql() {
  local attempt
  for attempt in {1..30}; do
    if mysqladmin --protocol=TCP -h "$mysql_host" -P "$mysql_port" ping --silent >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "MySQL 在 30 秒内未就绪，请检查 Homebrew 服务日志。" >&2
  exit 1
}

command_name="${1:-status}"
case "$command_name" in
  start)
    brew services start "$mysql_service"
    wait_for_mysql
    echo "MySQL 已就绪：${mysql_host}:${mysql_port}"
    ;;
  status)
    if mysqladmin --protocol=TCP -h "$mysql_host" -P "$mysql_port" ping --silent >/dev/null 2>&1; then
      echo "MySQL 服务：运行中（${mysql_host}:${mysql_port}）"
    else
      echo "MySQL 服务：未运行"
      exit 1
    fi
    if [[ -n "$mysql_user" && -n "$mysql_password" ]]; then
      if MYSQL_PWD="$mysql_password" mysql --protocol=TCP --default-character-set=utf8mb4 -h "$mysql_host" -P "$mysql_port" -u "$mysql_user" -N -e "SELECT 1" "$mysql_database" >/dev/null 2>&1; then
        echo "应用数据库：连接成功（${mysql_database}）"
      else
        echo "应用数据库：无法使用当前 .env 连接" >&2
        exit 1
      fi
    else
      echo "应用数据库：未配置 .env"
    fi
    ;;
  migrate)
    if [[ "${2:-}" == "--apply" ]]; then
      require_app_config
      wait_for_mysql
      .venv/bin/python -m backend.migrate_sqlite_to_mysql --sqlite backend/data/voicescope.sqlite3 --apply
    elif [[ -z "${2:-}" ]]; then
      .venv/bin/python -m backend.migrate_sqlite_to_mysql --sqlite backend/data/voicescope.sqlite3
    else
      usage
      exit 2
    fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 2
    ;;
esac
