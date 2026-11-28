# Linux 部署说明

## Docker Compose

在安装 Docker Engine 和 Docker Compose v2 的 Linux 主机上，进入本项目目录，复制配置示例：

```bash
cp .env.example .env
```

编辑 `.env`，设置 `PRACTICE_ADMIN_PASSWORD`，至少 12 个字符。管理用户名默认为 `admin`，可通过 `PRACTICE_ADMIN_USERNAME` 修改。启动服务：

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100
```

首次构建需能够下载 Python 基础镜像及 PyPI 依赖。服务以非 root 用户运行，账号数据和密钥写入独立命名卷 `/data`。`/healthz` 用于健康检查，只返回调度器是否运行，不返回账号信息；其余页面、静态资源和 API 均要求管理台认证。

默认只监听宿主机 `127.0.0.1:8765`。如从自己的电脑访问，在本地终端建立转发：

```bash
ssh -N -L 8765:127.0.0.1:8765 user@server
```

保持 SSH 连接，浏览器打开 `http://127.0.0.1:8765/`，在登录框中输入管理台凭据。Linux 不具备 Windows 设备定位能力，签到账号应使用自定义坐标。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `PRACTICE_ADMIN_USERNAME` | `admin` | 服务器模式的管理台登录名，不是实习平台账号 |
| `PRACTICE_ADMIN_PASSWORD` | 无 | 管理台密码，至少 12 个字符，Compose 启动时必须配置 |
| `PRACTICE_BIND` | `127.0.0.1` | Docker 在宿主机上发布端口的监听地址 |
| `PRACTICE_PORT` | `8765` | Docker 在宿主机上发布的端口 |
| `PRACTICE_PUBLIC_ORIGIN` | 空 | 反向代理对外提供的完整站点源，例如 `https://practice.example.com`，不包含路径 |
| `PRACTICE_DATA_DIR` | Docker 中为 `/data` | 源码运行时可指定持久化数据目录，命令行 `--data-dir` 优先 |

源码服务器模式也支持通过 `PRACTICE_ADMIN_PASSWORD_FILE` 读取 UTF-8 密码文件；它不能与 `PRACTICE_ADMIN_PASSWORD` 同时设置。若使用 Compose secrets，可将密码文件挂载至容器，再调整 Compose 环境变量使用该路径。

## HTTPS 反向代理

需要通过域名访问时，保留容器在宿主机回环地址上的监听，并在 `.env` 设置：

```dotenv
PRACTICE_PUBLIC_ORIGIN=https://practice.example.com
```

重新创建服务以应用环境变量：

```bash
docker compose up -d
```

以下为 Nginx HTTPS 站点配置示例。域名、证书和私钥路径需替换为实际值：

```nginx
server {
    listen 443 ssl;
    server_name practice.example.com;
    ssl_certificate /etc/letsencrypt/live/practice.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/practice.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $http_host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

设置 `PRACTICE_PUBLIC_ORIGIN` 后，请使用所配置的域名访问管理台。程序校验实际请求的 Host 和 Origin，不使用任意 `X-Forwarded-Host` 来放宽来源检查。HTTP Basic 认证应通过 HTTPS 或 SSH 转发使用。

## 数据持久化与更新

`practice-data` 命名卷保存以下数据：

- `practice.sqlite3`：账号、调度配置、每日随机执行时间、执行记录及邮件设置。
- `secret.key`：凭据加密密钥，首次启动时创建，文件权限为 `0600`。
- `calendar.json`：可选的年度日历配置。

容器更新不会清空该卷。更新代码后执行：

```bash
docker compose up -d --build
```

备份前先停止服务，复制整个数据目录，完成后重新启动：

```bash
docker compose stop
docker compose cp practice-tool:/data ./practice-backup
docker compose start
```

应将数据库与原始 `secret.key` 一并保留。恢复后，数据目录需允许容器用户（UID/GID `10001`）读写。Windows 的 DPAPI 凭据与 Linux 的 Fernet 凭据不互通，跨平台复制数据库后应重新填写账号和 SMTP 密码。

`docker compose down` 保留命名卷；`docker compose down -v` 会删除命名卷及其中的数据。

## 节假日日历

内置日历覆盖 2026、2027 年，包含官方通知中完整的节假日放假区间及调休上班日期：

- [2026 年部分节假日安排（国务院办公厅通知，政府网站转载）](https://www.shache.gov.cn/scx/c118335/202511/5048185cc52b402eaf7115296e881827.shtml)
- [2027 年部分节假日安排（国务院办公厅通知，国家林草局转载）](https://www.forestry.gov.cn/lyj/1/szxx/20261105/695102.html)

“仅节假日”指通知列明的完整放假区间，包含假期内的周末和调休日，不包含普通周末。“仅工作日”会排除这些放假日期，并包含调休上班日。“节假日和周末”则排除调休上班日。

数据目录中的 `calendar.json` 可补充新的年份，或完整覆盖已有年份。格式参见 [calendar.example.json](calendar.example.json)：

- 顶层键为四位年份字符串。
- `holidays` 为该年度完整的节假日放假日期列表，日期格式为 `YYYY-MM-DD`。
- `workdays` 为该年度调休上班日期列表。
- 两个列表不能包含其他年份的日期，同一天不能同时出现在两个列表中。

更新后重启服务以加载。例如，准备好完整的 `calendar.json` 后：

```bash
docker compose cp ./calendar.json practice-tool:/data/calendar.json
docker compose restart
```

若某年没有可用日历，依赖日历的计划会显示错误并暂停；每天、周一至周五和仅周末模式不依赖年度日历。

## 随机时间调度

账号编辑页可将执行时间设为“时间段内随机”，并填写当天的开始及结束时间。两者必须在同一天，且结束时间晚于开始时间；执行时间精度为分钟。

系统为每个符合日期规则的账号生成当日执行时间，并保存在 SQLite 中。刷新管理台和重启容器均不会重新抽取。可选分钟多于一个时，系统排除上一个执行日的计划时间；在已开始的时段内首次启用时，仅从剩余时间中选择。

若服务在当日时间段结束后恢复运行，随机计划不会自动补签。固定时间模式保留当天到期计划的补执行行为。所有日期和时间判断均使用北京时间（UTC+8）。
