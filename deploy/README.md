# 公网部署到现有云服务器（Cloudflare 临时 HTTPS）

目标服务器：`root@<服务器IP>`，默认 SSH 端口 22。当前工作环境尚无 SSH 原始凭据，亦没有可用的公网 SSH TCP 通道，因此**尚未在目标服务器执行安装**。

无需 Cloudflare 账号或自有域名，Quick Tunnel 会分配 `https://随机名称.trycloudflare.com`。这是临时团队试用方案，隧道进程重启可能更换地址，Cloudflare 不提供此模式的 SLA。之后有域名时可切换为命名隧道，数据库无需迁移。

## 服务器控制台安装

1. 下载交付的 `web3-scout-deploy.tar.gz` 和 `.sha256` 校验文件，通过云厂商网页控制台的文件上传功能或你自己电脑上的 SCP 上传到服务器 `/root/`。包不包含数据库、测试账号、密码、私钥或其他凭据。不要在聊天中发送服务器密码或私钥。
2. 在服务器控制台终端运行：

```sh
mkdir -p /opt/web3-scout
# 解压到新目录；升级前请保留原代码包，数据库在独立持久卷中。
cd /root
sha256sum -c web3-scout-deploy.tar.gz.sha256
tar -xzf /root/web3-scout-deploy.tar.gz -C /opt/web3-scout
cd /opt/web3-scout
bash deploy/deploy.sh
```

脚本适用于 Linux Docker Engine（amd64/arm64）。Debian/Ubuntu 在未安装 Docker 时会通过系统 APT 安装；其他系统请先自行安装 Docker Engine。首次运行会在服务器终端询问管理员账号、密码及确认密码，密码至少 14 字符，不回显。仅在数据库中保存带随机盐的 scrypt 密码哈希；部署文件和日志不包含密码。

脚本会构建镜像，建立专属网络和持久卷，启动应用，等待健康检查，再创建 Cloudflare 隧道并输出 HTTPS 地址。已有 Docker 服务不受修改，重名但不属于本应用的资源会导致脚本安全停止。更新应用前会创建一致性数据库备份；新版启动失败会尝试恢复旧容器。

应用不在宿主机公开 3000/80/443 端口，仅 Cloudflare 隧道可达。服务器需正常解析 DNS，并允许访问 Docker Hub，以及 Cloudflare API 的 TCP 443 和隧道的 TCP 7844。脚本强制 HTTP/2，避免要求 UDP QUIC。若云厂商封锁隧道或 Docker 下载，需在该服务器网络侧解除限制。

## 验证

1. 外部浏览器打开脚本输出的 HTTPS 地址，应显示团队登录页。
2. 未登录请求 `/api/state` 应得到 HTTP 401，而不是项目数据。
3. 用新建账号登录，新增一个测试项目、添加跟踪记录，然后刷新确认保存。
4. 在服务器重启应用 `docker restart web3-scout-app` 后再次确认记录还在。
5. 把临时 HTTPS 地址发回本次聊天即可（不含密码），我可继续检查公网可达性与登录隔离。

**脚本打印出域名只证明取到了隧道地址，不代表外部验证通过。**

## 团队账号

新增账号或重置密码（旧会话随即失效）：

```sh
docker exec -it web3-scout-app python3 manage.py user-add alice
```

查看账号 / 移除离组成员：

```sh
docker exec web3-scout-app python3 manage.py user-list
docker exec web3-scout-app python3 manage.py user-delete alice
```

没有公开注册入口。成员均可研究、录入、编辑与删除项目，没有细分角色权限。界面中的项目负责人仍为研究归属；操作动态使用服务端核验后的登录账号。会话最长 12 小时，退出登录撤销会话；密码重置会撤销该账号所有会话。登录尝试有 15 分钟限流。

## 维护

```sh
# 只查看最近日志；不要将整个服务器环境或凭据贴出
docker logs --tail 60 web3-scout-app
docker logs --tail 100 web3-scout-tunnel

# 重新部署当前目录的代码；保留数据库和已运行的隧道
bash deploy/deploy.sh

# 应用进程重启，不重建数据卷
docker restart web3-scout-app

# 隧道重启后需重新从日志查看临时地址
docker restart web3-scout-tunnel
```

备份并导出到宿主机（备份包含账号哈希与研究数据，请限制访问）：

```sh
# 每次使用一个不同的新文件名；命令拒绝覆盖已有备份
docker exec web3-scout-app python3 manage.py backup /data/backups/manual-01.db
mkdir -p /root/scout-backups
chmod 700 /root/scout-backups
docker cp web3-scout-app:/data/backups/manual-01.db /root/scout-backups/manual-01.db
chmod 600 /root/scout-backups/manual-01.db
```

Docker 卷 `web3-scout-data` 保存数据库及备份。不要执行 `docker volume rm web3-scout-data` 或无选择的 `docker system prune --volumes`。服务器上的同卷备份不能防止整机损坏，请定期将导出的备份存放到独立安全存储。

## SSH 凭据与本环境的限制

本环境的 HTTPS 代理 Secret 不是 SSH 原始私钥，不能将代理占位变量写入私钥文件。正常 SSH 自动部署需要两个独立条件：到服务器 22 端口的获准 TCP 通道，以及本机安全私钥 / agent 身份与经核验的服务器主机公钥。当前运行时的 TCP 代理面向获准的 VPN 私网地址，不提供到目标公网 IP 的连接能力；追加 HTTP 域名白名单不能解决 SSH 连接。

若后续平台提供可用公网 SSH 执行通道，可生成临时部署密钥，在服务器 `authorized_keys` 中添加其公钥，保持私钥在受保护的执行环境内，并通过可信控制台核验主机指纹。不要关闭主机公钥校验，不要把服务器 root 私钥或密码发到聊天。部署完成后应撤销临时密钥。

## 本地验证结果

- 17 项后端与认证测试通过。
- 原项目流程浏览器验证通过。
- 登录重定向、错误密码、登录后录入、真实操作人、退出及接口隔离浏览器验证通过。
- 固定摘要的 Docker 镜像构建通过；非 root、只读根文件系统、账号初始化与持久卷读写通过。
- 同一持久卷连续运行部署脚本两次，备份及数据保留验证通过（`SCOUT_SKIP_TUNNEL=1`，未向公网开启测试隧道）。
- 固定摘要的 Cloudflare 镜像已验证能运行，并核对 Quick Tunnel 参数。
- 目标服务器连接、远端安装和实际公网 HTTPS 可达性尚未验证。
