#!/usr/bin/env bash
set -euo pipefail
run_dir=$1
py=$2
probe=$3
source_version() {
  docker exec -e "MYSQL_PWD=$OE_TEST_PASSWORD" "$1" mysql \
    -uroot --socket=/var/run/mysqld/mysqld.sock -BN -e "$2"
}
await_sql() {
  for _ in $(seq 1 90); do
    if source_version "$1" "SELECT VERSION()" 2>/dev/null; then return; fi
    sleep 2
  done
  docker logs "$1" >&2
  return 1
}
for volume in oe-src-data oe-src-socket oe-restore-data oe-backup; do docker volume create "$volume"; done
docker pull "$MYSQL_IMAGE" &
mysql_pull=$!
docker pull "$XTRA_IMAGE" &
xtra_pull=$!
wait "$mysql_pull"
wait "$xtra_pull"
docker run -d --name oe-src --network none \
  -e "MYSQL_ROOT_PASSWORD=$OE_TEST_PASSWORD" \
  -v oe-src-data:/var/lib/mysql -v oe-src-socket:/var/run/mysqld \
  "$MYSQL_IMAGE" --server-id=41 --socket=/var/run/mysqld/mysqld.sock
for _ in $(seq 1 90); do
  if docker logs oe-src 2>&1 | grep -F 'MySQL init process done. Ready for start up.' >/dev/null; then break; fi
  sleep 2
done
await_sql oe-src
source_version oe-src \
  "CREATE DATABASE oe_fixture;
   CREATE TABLE oe_fixture.events (id int primary key, label varchar(60)) ENGINE=InnoDB;
   INSERT INTO oe_fixture.events VALUES (11, 'alpha'), (22, 'beta');"
docker run -d --name oe-xtra --network none --user 0 --entrypoint /bin/sh \
  -v oe-src-data:/var/lib/mysql:ro -v oe-src-socket:/var/run/mysqld:ro \
  -v oe-backup:/backup -v oe-restore-data:/restore-data \
  "$XTRA_IMAGE" -c 'sleep 1200'
docker exec oe-xtra xtrabackup --version
timeout 150 "$py" "$probe" "$run_dir/baseline.py" baseline
timeout 150 "$py" "$probe" "$run_dir/candidate.py" candidate
docker exec oe-xtra test -s /backup/candidate/xtrabackup_checkpoints
docker exec oe-xtra xtrabackup --prepare --target-dir=/backup/candidate > "$run_dir/prepare.log" 2>&1 || {
  cat "$run_dir/prepare.log"; exit 1;
}
grep -F 'completed OK!' "$run_dir/prepare.log"
source_version oe-src "INSERT INTO oe_fixture.events VALUES (33, 'after-backup');"
docker exec oe-xtra xtrabackup --copy-back --target-dir=/backup/candidate --datadir=/restore-data \
  > "$run_dir/copyback.log" 2>&1 || { cat "$run_dir/copyback.log"; exit 1; }
grep -F 'completed OK!' "$run_dir/copyback.log"
uid=$(docker exec oe-src id -u mysql)
gid=$(docker exec oe-src id -g mysql)
docker exec oe-xtra chown -R "$uid:$gid" /restore-data
docker run -d --name oe-restored --network none \
  -v oe-restore-data:/var/lib/mysql \
  "$MYSQL_IMAGE" --server-id=42 --socket=/var/run/mysqld/mysqld.sock
await_sql oe-restored
query="SELECT GROUP_CONCAT(CONCAT(id, ':', label) ORDER BY id) FROM oe_fixture.events;"
test "$(source_version oe-src "$query")" = '11:alpha,22:beta,33:after-backup'
test "$(source_version oe-restored "$query")" = '11:alpha,22:beta'
echo 'Prepared XtraBackup restored both expected rows to a separate MySQL instance'
