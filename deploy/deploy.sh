#!/usr/bin/env bash
# Run from the extracted application on the target Linux server.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
prefix="${SCOUT_DEPLOY_PREFIX:-web3-scout}"
[[ "$prefix" =~ ^[a-z][a-z0-9-]{0,40}$ ]] || { echo 'Invalid container prefix' >&2; exit 1; }
app="$prefix-app"
volume="$prefix-data"
network="$prefix-network"
tunnel="$prefix-tunnel"
rollback="$prefix-rollback"
image="$prefix:$(date -u +%Y%m%d%H%M%S)-$$"
cloudflared='cloudflare/cloudflared@sha256:faa8e0a1475f137c910be6025f43c8e4092fdd35461b31e19fd1390657a1ff39'
# Always target the server's local Docker daemon, not an inherited remote context.
d() { env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock "$@"; }
if ! command -v docker >/dev/null; then
    if [[ $(id -u) != 0 ]]; then echo '需要安装 Docker，请以 root 在服务器终端运行。' >&2; exit 1; fi
    if ! command -v apt-get >/dev/null; then echo '请先按系统官方说明安装 Docker Engine，再运行脚本。' >&2; exit 1; fi
    echo '安装系统软件源提供的 Docker（保持软件包签名验证）。'
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
    systemctl enable --now docker
fi
d info >/dev/null
# Refuse to touch any identically named resource not created by this script.
owned() {
    local kind="$1" name="$2" owner
    if d "$kind" inspect "$name" >/dev/null 2>&1; then
        if [[ "$kind" == container ]]; then owner=$(d container inspect --format '{{index .Config.Labels "com.web3-scout.stack"}}' "$name");
        else owner=$(d "$kind" inspect --format '{{index .Labels "com.web3-scout.stack"}}' "$name"); fi
        [[ "$owner" == "$prefix" ]] || { echo "资源 $name 已存在且不属于本应用；停止以保护现有服务。" >&2; exit 1; }
    fi
}
for name in "$app" "$tunnel" "$rollback"; do owned container "$name"; done
owned network "$network"; owned volume "$volume"
# Build before stopping the previous application. Build failure leaves it running.
d build --tag "$image" .
if ! d volume inspect "$volume" >/dev/null 2>&1; then d volume create --label "com.web3-scout.stack=$prefix" "$volume" >/dev/null; fi
if ! d network inspect "$network" >/dev/null 2>&1; then d network create --label "com.web3-scout.stack=$prefix" "$network" >/dev/null; fi
users=$(d run --rm --network none --mount "type=volume,src=$volume,dst=/data" "$image" python3 -c 'import server; server.init_db(); db=server.connect(); print(db.execute("SELECT count(*) FROM users").fetchone()[0])')
if [[ "$users" == 0 ]]; then
    echo '首次部署：创建团队管理员。密码只在当前终端输入，不会显示或写入部署包。'
    [[ -t 0 ]] || { echo '请在交互式终端运行 deploy.sh，以安全创建管理员。' >&2; exit 1; }
    read -r -p '管理员用户名 [admin]: ' username
    username="${username:-admin}"
    d run --rm -it --network none --mount "type=volume,src=$volume,dst=/data" "$image" python3 manage.py user-add "$username"
fi
# Keep a consistent snapshot before every release, including the first deployment.
backup="/data/backups/before-$(date -u +%Y%m%d%H%M%S)-$$.db"
d run --rm --network none --mount "type=volume,src=$volume,dst=/data" "$image" python3 manage.py backup "$backup"
if d container inspect "$rollback" >/dev/null 2>&1; then d rm -f "$rollback" >/dev/null; fi
has_old=0
if d container inspect "$app" >/dev/null 2>&1; then
    has_old=1
    d stop "$app" >/dev/null
    d rename "$app" "$rollback"
fi
restore() {
    d rm -f "$app" >/dev/null 2>&1 || true
    if [[ "$has_old" == 1 ]]; then d rename "$rollback" "$app"; d start "$app" >/dev/null; fi
    echo '新版启动失败，已尝试恢复旧容器。数据库备份保留在持久卷中。' >&2
}
if ! d run -d --name "$app" --label "com.web3-scout.stack=$prefix" --restart unless-stopped \
    --network "$network" --network-alias app --mount "type=volume,src=$volume,dst=/data" \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges \
    --log-opt max-size=10m --log-opt max-file=3 "$image" >/dev/null; then restore; exit 1; fi
healthy=0
for _ in {1..30}; do
    if [[ $(d inspect --format '{{.State.Health.Status}}' "$app") == healthy ]]; then healthy=1; break; fi
    sleep 1
done
if [[ "$healthy" != 1 ]]; then d logs --tail 30 "$app"; restore; exit 1; fi
if [[ "${SCOUT_SKIP_TUNNEL:-0}" == 1 ]]; then echo '应用健康检查通过。按请求跳过公网隧道（仅用于本地验证）。'; exit 0; fi
# Do not replace a healthy existing tunnel: keeping it preserves its current URL.
if ! d container inspect "$tunnel" >/dev/null 2>&1; then
    d pull "$cloudflared"
    d run -d --name "$tunnel" --label "com.web3-scout.stack=$prefix" --restart unless-stopped \
        --network "$network" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
        --cap-drop ALL --security-opt no-new-privileges --log-opt max-size=10m --log-opt max-file=3 \
        "$cloudflared" tunnel --no-autoupdate --protocol http2 --url http://app:3000 >/dev/null
else
    d start "$tunnel" >/dev/null
fi
url=''
for _ in {1..45}; do
    url=$(d logs --tail 200 "$tunnel" 2>&1 | sed -nE 's/.*(https:\/\/[a-z0-9-]+\.trycloudflare\.com).*/\1/p' | tail -n 1)
    if [[ -n "$url" ]]; then break; fi
    sleep 1
done
if [[ -z "$url" ]]; then
    echo '应用已启动，但未取得公网地址。请运行 docker logs --tail 100 '"$tunnel"' 检查隧道连接。' >&2
    exit 1
fi
printf '\n团队试用 HTTPS 地址：%s\n' "$url"
printf '账号：你刚创建的团队账号。密码不会输出。\n'
printf '提醒：临时域名可能在隧道进程重启后变化；数据保存在 Docker 持久卷 %s 中。\n' "$volume"
printf '请在外部浏览器确认登录页可用，登录后新增一条测试记录。\n'
