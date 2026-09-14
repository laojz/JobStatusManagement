# 已部署 Jobs Status Manager 人工验证与故障排查手册

## 1. 目的与结论边界

本手册供人工操作员在 Ubuntu 上验证已部署的 Jobs Status Manager。它覆盖进程、监听器、SQLite、NPM/OpenResty、DNS/TLS、QQ、LLM、IMAP、附件、知识库、投递、持久任务、重启恢复、备份和清理。

本手册不是生产就绪声明，也不把历史测试、部署完成陈述、本地假适配器结果或 SDK 方法存在性当作当前部署成功。每项能力只有在同一个本次 Run ID 下重新执行、留下合规证据并完成清理后，才能判定。

详细的 31 项用例、依赖、初始状态和执行记录位于 [已部署全流程验证程序与执行记录](evidence/deployed-functional-flow-2026-09-14.md)。本手册负责现场操作和首层排障，台账负责逐项记录。不得修改台账中的历史观察来制造当前成功，也不得用本手册替换台账记录。

每次验证必须满足以下协作约束：

1. 指定一名 Lead 操作员。只有 Lead 可以放行阶段、冻结外部动作、批准重试、批准一次重启和签署收尾。
2. 全员共用一个 Run ID。建议格式为 `DF-YYYYMMDD-UTC-HHMM-<短随机标识>`，不得含账号、openid、主机私密信息或凭据片段。
3. 其他操作员只执行 Lead 分配的互不重叠对象。不得让两人同时操作同一会话、PendingAction、通知、provider 消息、附件、数据库恢复对象或清理对象。
4. 每个阶段开始前检查前置阶段状态。第一层失败后停止依赖动作，不要继续制造连锁错误。

### 状态定义

| 状态 | 判定标准 |
|---|---|
| `PASS` | 本次 Run ID 下已实际执行，结果符合预期，证据、限制和清理记录完整。 |
| `FAIL` | 所需能力和审批可用，动作已执行，但实现、状态、副作用、安全边界或清理不符合预期。 |
| `BLOCKED` | 凭据、能力、审批、端点、账号、维护窗口、安全隔离或前置条件缺失，不能安全执行。 |
| `NOT_RUN` | 尚未执行，或因上游停止而不应开始。 |

QQ、LLM、IMAP、embedding/Chroma、入站文件、出站文件或图片能力被禁用、未配置或未获批准时，相关步骤记为 `BLOCKED`，不是 `FAIL`。上游尚未执行时，下游通常为 `NOT_RUN`。不要把 `BLOCKED` 改写成实现失败，也不要把本地测试结果改写成真实 provider `PASS`。

## 2. 使用前逐项复核默认值与候选值

下表仅是手册默认值或部署候选值，不是仓库保证。Lead 必须要求负责人逐项替换或明确批准，未完成的行会阻塞相关阶段。

| 项目 | 默认值或候选值 | 性质 | 执行前批准值 | 审批人和 UTC |
|---|---|---|---|---|
| 应用目录 | `/opt/jobs-status-manager/app` | 默认值，非仓库保证 | `<APP_DIR>` | `<alias/time>` |
| systemd 服务 | `jobs-status-manager` | 候选值，仓库未规定 supervisor 名称 | `<SERVICE>` | `<alias/time>` |
| 公网基础地址 | `https://panafish.icu` | 候选值 | `<PUBLIC_BASE>` | `<alias/time>` |
| QQ 回调路径 | `/qq/callback` | 候选值，实际值由部署配置决定 | `<CALLBACK_PATH>` | `<alias/time>` |
| 反向代理形态 | NPM/OpenResty 运行于 Docker | 候选拓扑 | `<PROXY_TOPOLOGY>` | `<alias/time>` |
| NPM upstream | `host.docker.internal:8000` | 候选值 | `<NPM_UPSTREAM>` | `<alias/time>` |
| schema head | `0008_qq_reply_targets` | 当前仓库目标，仍须核对部署制品 | `<SCHEMA_HEAD>` | `<alias/time>` |
| 本地监听地址 | `127.0.0.1` 或 `0.0.0.0` | 待审批值 | `<APP_BIND>` | `<alias/time>` |
| 本地监听端口 | `8000` | 仓库默认候选值，不可公网暴露 | `<APP_PORT>` | `<alias/time>` |
| 本机探针地址 | `127.0.0.1` | 默认候选值，须能到达批准监听器 | `<LOCAL_PROBE_HOST>` | `<alias/time>` |
| NPM 容器 | 无默认名称 | 必填候选值 | `<NPM_CONTAINER>` | `<alias/time>` |

只有所有相关值已替换或批准后，才在命令中使用。不要直接复制尖括号占位符执行。不要用探测公网端口的方式猜测配置。

可在当前 shell 中设置非秘密别名，减少抄写错误。不要在这里放凭据、token、openid 或数据库秘密：

```bash
APP_DIR='<approved-app-directory>'
SERVICE='<approved-systemd-service>'
PUBLIC_BASE='https://<approved-public-host>'
APP_BIND='<approved-local-bind-address>'
APP_PORT='<approved-local-port>'
LOCAL_PROBE_HOST='<approved-local-probe-host>'
NPM_CONTAINER='<approved-container-name>'
```

## 3. 安全不变量与证据规则

### 3.1 运行时不变量

- 同一验证状态只能有一个应用进程、一个 SQLite 数据库目标、一个 Starlette 监听器和一个 botpy `Client`。
- Starlette 拥有唯一 HTTP 监听器。botpy 是进程内客户端，不得另开 botpy HTTP 服务。
- 一个数据库、一个会话、一个 PendingAction、一个通知、一个 provider 消息和一个清理对象只能有一名操作员负责。
- 任何结果出现歧义后，不得自行重试、重放、重启或再次发送。
- 端口 `8000` 只用于受控本地或容器到宿主机 upstream，不得直接暴露到公网。
- 不得打印进程环境、`.env`、secret manager 返回值或包含秘密命令参数的完整命令行。
- 仓库 CLI 为执行重试所需的内部 task ID 只允许短暂出现在批准的私有服务器终端中。该终端必须关闭 session recording 和截图；完整 ID 不得进入共享证据、聊天、shell history 或复制的日志。

### 3.2 禁止进入 shell 历史、证据、截图和日志摘录的内容

绝不记录以下内容：

- 凭据、密码、API key、`Authorization` 头、签名、challenge token、私钥。
- 完整 openid、event ID、message ID、provider ID 或 trace ID。
- provider 原始 payload、QQ 原始事件、prompt、LLM 响应。
- 邮件正文、邮件地址、完整 IMAP cursor。
- 附件 URL、查询串、附件内容、下载 token。
- 数据库行、业务正文、备份内容。

允许记录以下有界信息：

- 主机名，以及不含查询参数的路径。
- HTTP 状态、QQ `err_code`、退出码。
- 有上限的计数、布尔值、枚举状态和状态变化，例如 `ConversationMessage +1`、`PendingAction=PENDING`。
- 语义标签，例如 `ordinary-chat-A`、`approved-mail-B`、`knowledge-doc-C`。
- 不可逆摘要前缀或脱敏 ID，例如 `sha256:<前 12 位>`、`evt:***a1b2`。不得记录可拼回完整值的多个片段。
- 脱敏后的日志来源和 UTC 时间窗口，不复制敏感日志正文。

发现敏感信息进入终端、日志、截图或证据时，立即停止。当前步骤按实际情况记 `FAIL`，后续外部步骤记 `NOT_RUN`，启动泄漏处理。不得只编辑证据来掩盖泄漏。

凭据、token、签名、payload、消息、邮件和文件内容在任何情况下都不得打印。内部 task ID 不属于可共享证据；即使批准重试需要它，也只能按 Phase 13 的私有非记录终端流程短暂使用。

### 3.3 日志读取边界

仓库没有提供可通用于原始日志正文的 sanitizer。不得先把原始 `journalctl` 或 `docker logs` 输出到终端，再人工删减。内容诊断只能使用以下任一来源：

1. 部署方已批准、在显示前完成脱敏的日志视图。
2. 日志 owner 针对一个明确 UTC 窗口制作的 sanitized summary。

如果没有安全的预脱敏视图，日志正文检查记为 `BLOCKED`。如只需证明指定窗口是否有日志，可使用直接汇总为行数的 metadata-only 管道，正文不会显示在终端：

```bash
set -o pipefail
sudo journalctl -u "$SERVICE" --since '<UTC start>' --until '<UTC end>' --no-pager --output=cat | wc -l
docker logs --since '<bounded-duration>' "$NPM_CONTAINER" 2>&1 | wc -l
```

行数只能证明窗口内的有界日志数量，不能证明错误内容或根因。证据中只记录批准的 sanitized log source、UTC window、行数或 owner 摘要，不复制原始正文。

### 3.4 持久任务的安全聚合基线

仓库的 `failures --include-stale` CLI 可以输出完整内部 task ID、related ID 和多行 `last_error`。任何通用 shell `sed`、`awk` 或正则管道都不能被视为有保证的 display-before-redaction 安全边界，因为错误续行或构造内容可能伪装成允许的记录。

例行验证和证据收集不得运行原始 `failures --include-stale`，也不得先执行它再做 shell 过滤或事后脱敏。该命令名只用于说明仓库能力和禁止的例行路径。

例行基线先记录 `/health` 的以下聚合字段：

- `durable_tasks`
- `failed_tasks`
- `stale_tasks`

当 `failed_tasks=0` 且 `stale_tasks=0` 时，这三个字段足以形成零任务基线，不需要任务明细。

当任一计数非零，或用例确实需要 kind/state 明细时，只能从以下安全来源取得：

1. 部署方批准的预脱敏 metadata source。
2. 批准的只读聚合证据操作员或只读聚合查询。

安全来源只允许返回 `kind`、`state`、`attempts`、`stale`、可选 `next_retry` 和有界计数。不得返回 ID、related ID、错误、数据库行、正文、payload 或任何内容字段，也不得执行写 SQL。

如果没有安全来源，详细持久任务检查以及依赖该明细的 retry 或 restart 决策均记为 `BLOCKED`。绝不回退到原始 CLI 或 shell sanitizer。

## 4. 执行前身份模板

开始 Phase 0 前填写。只填别名、批准主机名、路径、版本和摘要。

| 字段 | 本次值 |
|---|---|
| Run ID | `<RUN_ID>` |
| Lead 操作员 | `<LEAD_ALIAS>` |
| 部署制品、镜像摘要或 commit | `<ARTIFACT_ID>` |
| 主机和运行时 | `<HOST_ALIAS> / <Ubuntu-version> / <Python-version> / <SQLite-version>` |
| supervisor | `<SYSTEMD_SERVICE_OR_APPROVED_ALIAS>` |
| 工作目录 | `<APP_DIR>` |
| 环境注入别名 | `<SECRET_MANAGER_OR_PRIVATE_ENV_ALIAS>` |
| 数据库别名 | `<DB_TARGET_ALIAS>` |
| 批准 schema | `<APPROVED_SCHEMA_HEAD>` |
| 批准公网主机 | `<PUBLIC_HOSTNAME>` |
| 批准回调主机和路径 | `<CALLBACK_HOSTNAME><CALLBACK_PATH>` |
| 批准 QQ API 主机 | `<QQ_API_HOSTNAME>` |
| 批准 QQ Token 主机 | `<QQ_TOKEN_HOSTNAME>` |
| 测试 QQ 账号别名 | `<QQ_TEST_ACCOUNT_ALIAS>` |
| 测试 QQ 收件人别名 | `<QQ_TEST_RECIPIENT_ALIAS>` |
| 测试邮箱别名 | `<IMAP_TEST_MAILBOX_ALIAS>` |
| 能力快照 | `QQ=<state>; LLM=<state>; IMAP=<state>; embedding/Chroma=<state>; inbound-file=<state>; outbound-file=<state>; image=<state>` |
| 预备份负责人 | `<OWNER_ALIAS>` |
| 最终备份负责人 | `<OWNER_ALIAS>` |
| 清理负责人 | `<OWNER_ALIAS>` |
| 只读聚合证据负责人 | `<OWNER_ALIAS>` |

## 5. 分阶段人工验证

每个阶段都按“前置条件、动作、PASS、FAIL、BLOCKED、证据、首层排查、禁止动作、停止条件”执行。每个步骤另复制第 7 节的记录模板，并在 31 项台账中更新对应记录。不得预填结果。

### Phase 0：安全与身份

**前置条件**

- Lead、Run ID、角色边界和 UTC 窗口已确定。
- 第 2 节所有相关默认值和候选值已逐项批准。
- 测试账号、唯一收件人、邮箱、非敏感测试数据、备份和清理负责人已批准。

**动作**

1. 全员逐行复核第 4 节身份表。
2. Lead 确认每名操作员只拥有互斥对象。
3. 记录能力快照。禁用能力直接标记其相关步骤为 `BLOCKED`，不要试探调用。
4. 宣读第 8 节全局停止条件，并确认所有人知道冻结和交接方法。

**PASS 标准**：身份表完整，值彼此一致，角色互斥，安全和清理负责人明确。

**FAIL 标准**：开始动作后发现错误账号、错误端点、错误数据库、角色冲突或敏感信息泄漏。

**BLOCKED 标准**：缺少 Lead、Run ID、批准值、唯一收件人、备份负责人或清理负责人。

**证据**：身份表、审批人别名、UTC、能力快照和角色分工，不含秘密。

**首层排查**：先查审批记录和身份表，不查 provider。由 Lead 消除冲突或补齐批准。

**禁止动作**：使用未批准默认值、共享秘密、让两名操作员控制同一状态对象，或把禁用能力记为 `FAIL`。

**停止条件**：任何未批准值、负责人冲突或敏感数据进入记录时，冻结全部后续阶段。

### Phase 1：systemd、服务、进程与监听器

**前置条件**：Phase 0 已放行，服务名、工作目录、监听地址和端口已批准。当前步骤只读，不重启。

**安全命令**

```bash
sudo systemctl status "$SERVICE" --no-pager --lines=0
sudo systemctl show "$SERVICE" --property=LoadState,ActiveState,SubState,MainPID,ExecMainStartTimestamp,WorkingDirectory --no-pager
ps -eo pid=,ppid=,comm=
sudo ss -ltnp
```

不要运行会打印进程环境的命令，也不要采集含 secret 参数的完整命令行。`ps` 仅查看 PID、PPID 和命令名。如果 `systemctl status` 的进程行意外含秘密参数，立即停止截取并按泄漏流程处理。

**PASS 标准**

- unit 已加载，服务为 `active/running`，`MainPID` 唯一且工作目录与批准值一致。
- 只有一个应用进程和一个批准端口上的 Starlette 监听器。
- 没有第二 botpy 客户端或第二 HTTP 服务的迹象。

**FAIL 标准**：服务反复重启、进程数大于一、存在额外监听器、工作目录不符，或同一数据库可能被第二进程使用。

**BLOCKED 标准**：systemd 不是实际 supervisor，或操作员没有批准的只读查看权限。改用部署方批准的等价只读状态源，不要猜服务名。

**证据**：LoadState、ActiveState、SubState、PID 脱敏值、启动 UTC、工作目录别名、监听地址和端口、进程数量。

**首层排查**：先定位 supervisor 层，再看进程层，最后看监听器。日志内容只使用批准的预脱敏视图或本次 UTC 窗口的 owner sanitized summary。不要先重启。

**禁止动作**：打印进程环境或秘密参数、启动第二实例、用重启替代首层定位。

**停止条件**：第二进程、第二监听器、第二 botpy 客户端、错误工作目录或未知启动参数立即触发全局停止。

### Phase 2：本地 CLI、liveness 与 readiness

**前置条件**：Phase 1 通过，确认命令在已批准工作目录和原部署环境中运行。`APP_BIND` 和 `APP_PORT` 已审批。

**安全命令**

```bash
cd "$APP_DIR"
uv run python -m jobs_status_manager health
curl -sS -i --connect-timeout 5 --max-time 10 "http://$LOCAL_PROBE_HOST:$APP_PORT/live"
curl -sS -i --connect-timeout 5 --max-time 10 "http://$LOCAL_PROBE_HOST:$APP_PORT/health"
```

若服务绑定 `0.0.0.0`，本机探针通常仍应使用批准的本机地址，例如 `127.0.0.1`，不要把 `0.0.0.0` 当作远程目标地址。

**PASS 标准**

- CLI 输出语义为 `ready phase=6 schema_version=0008_qq_reply_targets`，退出码为 0。
- `/live` 为 HTTP 200，body 精确为 `{"status":"alive"}`。
- `/health` 只有在数据库可读、schema 精确为 head、产品能力 ready 或明确 local-only 时为 HTTP 200。
- `durable_tasks=degraded` 可以伴随 HTTP 200。它表示持久任务需处理，不是进程死亡。

**FAIL 标准**：CLI 指向非 head schema、`/live` 非 200 或 body 不精确、健康状态码与 JSON 语义不符。

**BLOCKED 标准**：无法进入原工作目录或无法使用原环境注入方式。不要临时选择另一个 `.env`。

**证据**：命令、退出码、HTTP 状态、固定 liveness body、health 中的 `database`、`schema_version`、`product_readiness`、`durable_tasks`、`failed_tasks` 和 `stale_tasks`。

**首层排查**：连接拒绝先查 Phase 1 的监听器。`/live` 成功但 `/health` 失败时，第一失败层是数据库、schema 或能力 readiness，不是进程层。

**禁止动作**：用 `0.0.0.0` 作为远程目标、启动第二服务探测端口、把 `/health` 当作 QQ 连通证明。

**停止条件**：发现 CLI 和服务使用不同数据库、出现旁路数据库，或操作员准备通过启动第二服务验证端口时立即停止。

`/health` 不是 QQ token、API、回调或收发连通性的证明。

### Phase 3：数据库、schema、完整性与预备份

**前置条件**：Phase 2 已确认同一工作目录和数据库别名，私有备份目录已批准，备份目标文件不存在且不等于线上数据库。

**例行验证命令和顺序**

```bash
cd "$APP_DIR"
uv run python -m jobs_status_manager health
APP_DATABASE_PATH='<approved-deployed-db-path>' uv run alembic current
APP_DATABASE_PATH='<approved-deployed-db-path>' uv run alembic check
uv run python -m jobs_status_manager integrity-check
```

随后按第 3.4 节记录 `/health` 的 `durable_tasks`、`failed_tasks` 和 `stale_tasks`。计数为零时直接形成零任务基线；计数非零或需要明细时，只使用批准的预脱敏 metadata source 或批准的只读聚合证据。只有 health、schema 和 integrity 全部有效后，才创建并隔离检查预备份：

```bash
mkdir -p './backups'
uv run python -m jobs_status_manager backup './backups/jobs-<RUN_ID>-pre.db'
uv run python -m jobs_status_manager restore-check './backups/jobs-<RUN_ID>-pre.db'
```

`migrate` 和 `bootstrap` 是部署或变更操作，不是例行人工验证检查。验证 Run 中发现 schema mismatch、缺少 schema 或缺少预期 bootstrap identity 时，立即停止本 Run，不得现场修复。已明确部署契约却不符合时记 `FAIL`；无法确认目标或预期 identity 时记 `BLOCKED`。

部署 owner 只有在独立批准的 change procedure 下，具备该变更自己的 pre-change backup 和 recovery plan 时，才可运行 `migrate` 或 `bootstrap`。变更完成后必须使用新的 Run ID 从 Phase 0 重新开始。`bootstrap` 会打印完整 identity ID，因此不得在本验证序列中调用，也不得把其原始输出纳入证据。

直接 Alembic 不读取 `.env`。上面的命令只有在 Lead 已核对绝对路径或工作目录解析后的数据库路径时才可执行。不得省略显式的 `APP_DATABASE_PATH`。

**执行顺序不可变**：read-only health 和 Alembic schema 检查，随后 integrity，随后第 3.4 节安全聚合基线，最后才是批准的 SQLite backup 和 isolated restore-check。

**PASS 标准**

- 目标数据库与身份表一致，schema 为 `0008_qq_reply_targets`，Alembic 无待生成升级。
- `integrity-check` 退出码为 0。
- 第 3.4 节已记录 health 聚合计数；非零时所需明细仅来自批准的安全聚合来源，且不含 ID、错误、行或内容。
- SQLite backup 成功，目标此前不存在，隔离 `restore-check` 成功且没有替换线上数据库。

**FAIL 标准**：已批准部署的 schema 或 bootstrap identity 缺失、数据库不可读、完整性失败、目标文件已存在、备份目标等于线上数据库、恢复检查失败，或出现旁路数据库。发现后停止当前 Run，不就地迁移或 bootstrap。

**BLOCKED 标准**：数据库路径或 bootstrap identity 预期未确认、health 计数非零但没有批准的安全聚合明细来源、无法确认私有备份位置、没有写备份权限，或无法证明 restore-check 隔离。

**证据**：数据库别名、工作目录别名、schema、退出码、integrity 状态、失败任务有界计数、备份文件别名和权限摘要。不得记录数据库行或备份内容。

**首层排查**：先核对目标路径解析，再核对 schema 和预期 bootstrap identity，随后检查完整性。任何不符都停止本 Run 并交给 deployment owner 的独立变更流程。需要聚合业务计数时，交给批准的只读证据操作员，只报告计数、枚举状态和脱敏摘要。不要发明数据库检查 CLI，不要输出生产行，不要执行写 SQL。

**禁止动作**：在验证 Run 中运行 `migrate` 或 `bootstrap`、省略数据库路径执行直接 Alembic、覆盖备份、把 restore-check 指向线上替换流程、输出数据库行、完整 ID、原始错误或备份内容。

**停止条件**：错误数据库、错误 schema、备份覆盖、线上替换恢复或完整性失败触发全局停止。

### Phase 4：NPM/OpenResty 容器到宿主机 upstream

**前置条件**：Phase 1 至 Phase 3 通过，确认 NPM/OpenResty 确实运行于 Docker，容器名和 upstream 已批准。

**安全命令与动作**

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect --format '{{.Name}} network={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}}' "$NPM_CONTAINER"
docker exec "$NPM_CONTAINER" getent hosts host.docker.internal
```

若容器内已有批准的 HTTP 客户端，可从容器内部探测 upstream 的 `/live` 和 `/health`。不要临时安装工具、不要把 token 放进命令，也不要打印完整容器环境或完整配置。示例仅在客户端已存在且 upstream 值已批准时使用：

```bash
docker exec "$NPM_CONTAINER" curl -sS -i --connect-timeout 5 --max-time 10 'http://host.docker.internal:8000/live'
```

**绑定地址说明**

- 应用绑定 `127.0.0.1:8000` 时，只接受宿主机本地网络命名空间的连接。普通 Docker bridge 容器访问宿主机时通常不能到达该 loopback。
- 应用绑定 `0.0.0.0:8000` 时，可接受宿主机各接口连接，包括受控 Docker bridge upstream，但必须由宿主机防火墙、安全组和发布规则保证端口 `8000` 不对公网开放。
- 如果部署采用 host network、专用 bridge、Unix socket 或其他方案，按批准拓扑验证，不能套用候选值。

**PASS 标准**：容器运行，`host.docker.internal` 或批准宿主机别名可解析，容器内 upstream `/live` 返回精确 liveness，`/health` 语义与 Phase 2 一致，应用 bind 与拓扑匹配。

**FAIL 标准**：容器无法解析宿主机、连接错误目标、upstream 连接拒绝、返回非应用响应，或端口 `8000` 已公网暴露。

**BLOCKED 标准**：实际拓扑未知、容器无只读权限或无可用内部 HTTP 客户端。不要修改运行容器来完成验证。

**证据**：容器别名、network mode、解析结果摘要、upstream 主机和端口、HTTP 状态、固定 body、应用 bind，不记录完整配置。

**首层排查**：依次检查容器状态、名称解析、路由、宿主机 bind、监听器和应用响应。不要先改 NPM 配置。

**禁止动作**：暴露 8000、打印容器环境、临时安装工具、重建容器或启动第二应用。

**停止条件**：发现 8000 公网开放、意外 upstream、第二应用监听器或准备重建容器时停止并交接。

### Phase 5：公网 DNS、TLS、NPM `/live` 与 `/health`

**前置条件**：Phase 4 通过，公网主机和 DNS 记录已批准，外部探针窗口已放行。

**安全命令**

```bash
dig +short '<approved-public-hostname>' A
dig +short '<approved-public-hostname>' AAAA
curl -sS -i --connect-timeout 10 --max-time 20 "$PUBLIC_BASE/live"
curl -sS -i --connect-timeout 10 --max-time 20 "$PUBLIC_BASE/health"
```

如需核对台账中单独批准的 DNS 名称，例如 `bot.panafish.icu`，另行运行同样的 `dig`。不要从历史记录预填当前地址。

**PASS 标准**

- DNS 只返回批准地址。
- TLS 证书主机匹配批准域名，连接没有证书错误。
- 公网 `/live` 为 HTTP 200，body 精确为 `{"status":"alive"}`。
- 公网 `/health` 状态与本地同一应用的 readiness 语义一致。

**FAIL 标准**：未知 IP、证书错配、意外跳转、错误主机、NPM 路由错误、HTTP 502 或 504。502/504 不是部分成功，也不能作为应用健康证据。

**BLOCKED 标准**：DNS/TLS 变更尚未完成、入口未批准或外部探针被维护策略禁止。

**证据**：A/AAAA 记录摘要、证书主机摘要、HTTP 状态、`server` 头、固定状态字段、UTC。URL 只保留主机和无查询路径。

**首层排查**：先 DNS，再 TLS，再 NPM 路由，再容器到宿主机 upstream，再应用监听器。不要因为公网失败就启动第二应用进程。

**禁止动作**：沿用历史 DNS 结果、忽略证书错误、把 502/504 记为通过、直接探测或开放公网 8000。

**停止条件**：DNS 指向未知地址、证书主机不符、入口到达未知服务或 callback 主机发生偏移。

### Phase 6：QQ 启动、Token、API 与 `op=13`

**前置条件**：Phase 0 至 Phase 5 已放行，`QQ=enabled`，凭据通过私有方式注入，测试账号、唯一收件人、QQ API 主机、Token 主机和真实回调路径已批准。

**动作**

1. 从部署配置审批记录确认回调路由。不得从 `/qq/callback` 候选值、仓库默认 `/webhooks/qq` 或历史平台配置推断实际路径。
2. 通过批准的预脱敏启动日志视图或日志 owner sanitized summary，确认启动只创建一个 botpy client、一个 transport 和一个 gateway。没有安全视图时此项 `BLOCKED`。不要打印配置值或原始日志。
3. 由已批准的 QQ 平台流程观察 Token 获取、刷新和 API 访问结果。不得在本手册执行任务中新增 provider 调用。
4. 在 QQ 平台配置已批准回调并触发真实 `op=13` URL challenge。不要复制 challenge token、签名或原始 payload。

**PASS 标准**：API 和 Token 使用批准的 HTTPS 主机；启动资源唯一；平台接受 `op=13`；服务保持健康；只记录 HTTP 状态、QQ `err_code` 和脱敏 ID。

**FAIL 标准**：400/401/403/503、端点意外、凭据不完整、路由不符、第二 client、challenge 响应错误或秘密泄漏。具体 HTTP 分类见第 6 节。

**BLOCKED 标准**：QQ 禁用、凭据或端点未批准、平台无 challenge 窗口、测试账号不可用。

**证据**：SDK/制品版本、批准主机名、回调路径、启动状态、HTTP 状态、QQ `err_code`、脱敏 trace。健康端点不能单独证明 QQ 连通。

**首层排查**：先核对能力 gate 和配置主机，再核对启动资源，再核对回调路由和 NPM，最后看 provider 状态。不要更换到未批准 SDK 旧端点。

**禁止动作**：打印凭据或 token、猜测回调路径、切换到未批准端点、创建第二 botpy client。

**停止条件**：未知 QQ 主机、错误回调、凭据泄漏、第二 client 或平台请求身份不明。

### Phase 7：签名 C2C 文本、持久化、ACK 与幂等

**前置条件**：Phase 6 通过，批准账号可发送 C2C 文本，只读聚合证据操作员已就位。

**动作**

1. 从批准账号发送一条非敏感语义标签 `signed-c2c-text-A`。
2. 核对事件含 timestamp，应用按实现要求使用 Ed25519 对 timestamp 与原始 body 组合验签。
3. 核对入站事实先持久化，随后才返回普通 ACK `{"op":12,"d":0}`。
4. 仅通过 QQ 官方 redelivery 或批准的精确重投机制重投同一个真实 event/message。不能发送一条“内容相同但 ID 不同”的新消息来证明幂等。
5. 由只读证据操作员报告前后聚合计数和状态。

**PASS 标准**：首次创建一条 ConversationMessage 和一个排队或执行中的 AgentRun；ACK 精确；event ID 或 message ID 任一重复即可判重；精确重投不创建第二个 ConversationMessage、AgentRun、QQ 身份或 UserFile。

**FAIL 标准**：验签绕过、timestamp 规则失效、持久化前 ACK、ACK body 不符、重复事实或错误账号被接受。

**BLOCKED 标准**：无法安全发送真实 C2C，或平台不提供批准的精确 redelivery。redelivery 子项可单独 `BLOCKED`。

**证据**：ACK、签名接受布尔值、持久化先于 ACK 的顺序摘要、四类对象计数、同一脱敏 hash。不得记录签名、body 或完整 ID。

**首层排查**：先入口和签名层，再持久化事务，再 ACK，再幂等键。ACK 成功但后续无回复属于 Phase 8 或 Phase 12，不应改写 Phase 7 的入站结果。

**禁止动作**：记录签名或原始 body、用新消息冒充精确 redelivery、重复发送来掩盖无回复。

**停止条件**：持久化前 ACK 或任何重复持久事实触发全局停止。

### Phase 8：普通 LLM 对话与只读应用查询

**前置条件**：Phase 7 入站通过，`LLM=enabled`，批准测试 Application 基线可读。

**动作**

1. 发送普通对话语义标签 `ordinary-chat-B`，不含写请求、个人信息或附件。
2. 分别发送应用列表、单项状态、历史查询的批准语义标签。
3. 由只读证据操作员核对 Application、JobEvent、PendingAction 和 Outbox 聚合增量。

**PASS 标准**：普通对话得到一个被动回复；三个只读请求返回与批准基线一致的语义摘要；允许有只读 ToolCall/ToolResult；业务表写入增量为 0。

**FAIL 标准**：普通或只读请求产生业务写、越权工具、错误收件人、多个并行主 AgentRun 或不符合批准基线的结果。

**BLOCKED 标准**：LLM 禁用或未配置；没有批准测试 Application 时，只读应用子项 `BLOCKED`。

**证据**：语义标签、AgentRun 状态、工具名和调用次数、回复投递状态、四类业务对象增量。不得记录 prompt 或模型响应正文。

**首层排查**：先 AgentRun 是否排队和完成，再看 LLM 分类、工具调用、ReplyTarget 和发送结果。不要重试 AgentRun。

**禁止动作**：记录 prompt 或响应、手工重试 AgentRun、用写请求验证只读路径。

**停止条件**：任何未确认业务写、错误收件人或 prompt/响应泄漏。

### Phase 9：状态更新提案、确认、拒绝、重复确认与 no-op

**前置条件**：Phase 8 通过，测试 Application 和回滚路径已批准，目标状态确实与当前状态不同。

**动作**

1. 请求状态更新，但不确认。核对只创建唯一 proposal 和确认提示，业务事实不变。
2. 确认该 proposal。核对状态变化事务。
3. 新建另一 proposal 后拒绝。
4. 对第一项重复确认。
5. 对当前状态创建并确认 no-op。
6. 清理时如需恢复测试状态，也必须走确认路径并记账，不能直接写数据库。

**PASS 标准**

- 所有 mutation 前都有确认。
- 首次有效确认原子更新 Application，恰好创建一个 JobEvent 和一个对应 Outbox 效果。
- 拒绝不改变业务事实。
- 重复确认不产生第二次效果。
- no-op 可完成，但不创建 JobEvent 或状态变化 Outbox 效果。

**FAIL 标准**：确认前 mutation、拒绝产生写、重复确认重复写、no-op 产生事件、事务部分提交或确认对象错配。

**BLOCKED 标准**：没有可回滚测试 Application、目标状态未批准或无法安排只读聚合核验。

**证据**：各子项 PendingAction 状态、Application 状态摘要、JobEvent/Outbox 增量、确认码 hash 和事务时间摘要。

**首层排查**：先 proposal 创建和 `WAITING_USER_CONFIRMATION`，再确认解析，再 PendingAction 状态机，再原子事务和 outbox。不得直接写 SQL 修结果。

**禁止动作**：确认前 mutation、直接写数据库、重复确认来强迫执行、绕过确认恢复测试状态。

**停止条件**：确认前 mutation、重复 durable fact 或不原子副作用。

### Phase 10：IMAP 基线、新邮件、去重、MIME、LLM 分析与通知

**前置条件**：Phase 3 通过，`IMAP=enabled`，测试邮箱、发件通道、邮件清理策略已批准。工作邮件分析子项还要求 `LLM=enabled`。

**动作**

1. 记录 cursor 的不可逆摘要前缀，触发一次真实 poll，不发送新邮件。
2. 首次 cursor 应以当前最大 UID 建基线，故意不回填历史邮件。
3. 基线后发送一封非敏感测试邮件，poll 成功后再 poll，并按批准机制重放同 UID。
4. 验证失败场景时只能使用已存在的自然失败或安全隔离证据。IMAP gateway poll 失败，或 durable mail ingestion 完成前失败，不得推进 cursor。
5. 发送一封无职位词的普通邮件和一封批准的职位邮件。核对 MIME 解析、保守分类、严格 LLM 分析和通知或确认提案。

cursor 的责任边界结束于 durable mail ingestion。邮件已成功持久化后，cursor 可以正确推进，即使后续 consumer、LLM、proposal 或 notification 处理失败。这些下游失败从 durable task/event 状态恢复，不回滚 IMAP cursor。

**PASS 标准**

- 每次 poll 使用新的 SSL 连接并在结束后清理。
- 初始基线新增邮件数为 0，不回填历史。
- 新邮件仅持久化一次；同 UID、邮件、consumer 和 notification 不重复。
- gateway poll 失败或 durable mail ingestion 完成前失败时 cursor 不推进。
- durable mail ingestion 成功后，后续 consumer、LLM、proposal 或 notification 失败不阻止 cursor 正常推进；下游对象进入可恢复或明确失败的持久状态。
- 普通非职位邮件不调用 LLM；职位邮件可调用 LLM 严格结构化分析；状态建议只创建确认提案，不自动修改 Application。

**FAIL 标准**：历史回填、连接不清理、gateway poll 或 durable ingestion 完成前失败却推进 cursor、durable ingestion 未完成却丢失邮件、重复事实、普通邮件错误调用 LLM、自动更新业务状态或原始邮件泄漏。durable ingestion 成功后 cursor 推进而下游失败，不属于 cursor `FAIL`。

**BLOCKED 标准**：IMAP 禁用、账号未批准、无法制造新邮件。LLM 缺失时职位分析子项 `BLOCKED`，无需 LLM 的基线和普通分类仍可执行。

**证据**：连接次数、reset 布尔值、cursor hash 变化、邮件/consumer/notification 聚合计数、分类和分析状态。不得记录正文、地址或完整 cursor。

**首层排查**：依次检查 IMAP 认证和 TLS、基线 cursor、UID 搜索、MIME 解析和 durable mail ingestion。只有失败发生在 ingestion 完成前，才把 cursor 推进视为问题。若 ingestion 已完成，则转查 consumer、LLM、proposal、notification 的 durable 状态，不要求回滚 cursor。无新邮件先确认基线语义，不要把“故意不回填”判为失败。

**禁止动作**：记录邮件正文、地址或完整 cursor；重置或回退线上 cursor 来回填历史或重跑下游；把 durable ingestion 后的下游失败误判为 cursor 失败。

**停止条件**：原始邮件泄漏、gateway poll 或 durable ingestion 完成前失败却推进 cursor、重复持久化或未经确认自动改状态。durable ingestion 后的下游失败按持久任务流程处理，不触发 cursor 修复。

### Phase 11：入站附件、知识新增、索引、搜索、RAG 与移除

**前置条件**：Phase 7 通过，`inbound-file=enabled`，五类非敏感单文件和负例已批准。AddKnowledge 要求已成功解析文件。索引、SearchKnowledge 和 RAG 要求 `embedding/Chroma=enabled`。

**动作**

1. 逐一发送一个 `.txt`、`.md`、`.markdown`、`.pdf`、`.docx`，每条消息只含一个附件。
2. 另做一个不支持扩展名和一个多附件负例。
3. 核对下载仅走 HTTPS，执行 SSRF、MIME、大小、连接 timeout、总 deadline 和私网目标限制，文件进入私有存储，失败 `.part` 被清理。
4. 对一个已解析文件发起 AddKnowledge。先检查预览和 proposal，再确认；另建一项后拒绝。
5. 观察索引 `PENDING -> INDEXING -> READY`，失败时为 `FAILED`。
6. 对 READY 文档执行 SearchKnowledge 和一次 RAG，记录命中计数、分数区间和语义结果，不记录正文。
7. 确认 RemoveKnowledge 后立即重搜。`REMOVED` 文档必须立即失去搜索资格。
8. 只有应用已停止或 Lead 书面确认完全静默时，才可运行：

```bash
cd "$APP_DIR"
uv run python -m jobs_status_manager rebuild-chroma
```

**PASS 标准**

- 五类支持文件经过安全下载、私有存储和解析；不支持类型及多附件安全拒绝。
- AddKnowledge 确认前只有预览和 PendingAction，确认后创建一个关系型 KnowledgeDocument 及有界数量 KnowledgeChunk，拒绝不创建文档。
- SQLite 是事实源，Chroma 仅是可重建索引。
- READY 可搜和用于 RAG；FAILED 有有界错误；REMOVED 立即 0 命中。
- 停机或静默 rebuild 输出 typed counts，且无 partial failure。

**FAIL 标准**：允许 HTTP 或私网下载、MIME/大小边界失效、超时无界、`.part` 残留、跳过确认、拒绝仍写、REMOVED 仍可搜、运行态 rebuild 或 SQLite 与重建结果不一致。

**BLOCKED 标准**：入站文件或 embedding/Chroma 禁用、测试文件未批准、不能保证单附件、不能获得停机或静默窗口。

**证据**：扩展名、MIME 类别、字节区间、状态序列、文档和 chunk 有界增量、命中计数和分数区间、rebuild typed counts、存储权限摘要。不得记录 URL、查询串、文件内容或 RAG 文本。

**首层排查**：先 URL 方案和 SSRF，再 MIME/大小/timeout/deadline，再私有存储和 `.part`，再解析、确认、SQLite 文档、embedding、Chroma 和搜索过滤。

**禁止动作**：关闭下载安全边界、记录 URL 或内容、多附件静默处理、运行态 `rebuild-chroma`。

**停止条件**：不安全下载、内容泄漏、运行态 rebuild、绕过确认或 REMOVED 仍可见。

### Phase 12：QQ 被动、主动、文件与图片投递

**前置条件**：Phase 6 和相关业务阶段通过，唯一收件人再次确认。不同媒介分别按能力快照放行。

**动作**

1. 被动文本必须使用持久化的完整 ReplyTarget，包含 message ID 和 event ID，可用时包含正数 `msg_seq`。
2. 旧三参数 `reply(user_id, message_id, content)` 固定不能证明被动投递，不得作为验证路径。
3. 主动文本 target 只包含批准 openid，不带被动 message ID、event ID 或 `msg_seq`。
4. 生产 adapter 包含 `send_file` 和 `send_image`，但仓库没有支持操作员手工调用它们的生产 CLI 或通用 HTTP endpoint。
5. 文件和图片子测试只能通过已部署、已批准、会自然到达生产 adapter 的现有业务流程执行，并分别使用小型非敏感 fixture。
6. 若没有这样的已批准调用路径，对应文件或图片 egress 子测试记为 `BLOCKED`。不得编写临时脚本或直接调用 provider。
7. 每项都核对 provider 接受结果和批准客户端实际可见性。

**PASS 标准**

- 被动 ReplyTarget 完整，客户端恰好看到一条回复。
- 主动 target 没有被动字段，客户端恰好看到一条推送。
- 文件或图片由已批准的部署业务流程自然触发，且只有在 provider 接受并且客户端可见时通过。
- SDK 存在 `send_file` 或 `send_image` 方法本身不构成能力证明。

**FAIL 标准**：ReplyTarget 缺字段却降级发送、旧方法被当作证明、主动 target 携带被动字段、错误收件人、重复发送、provider 和客户端结果不一致。

**BLOCKED 标准**：对应 QQ、出站文件或图片能力禁用，provider 未确认支持，收件人未批准，或没有已部署且已批准的业务调用路径可以自然到达生产 adapter。

**证据**：媒介语义标签、字段存在性布尔值、QQ `err_code`、provider 状态、客户端可见布尔值、脱敏 provider ID、大小区间和 MIME 类别。

**首层排查**：先确认是否存在已部署且批准的业务调用路径，再查 target 类型和字段、唯一收件人、provider 请求分类、持久投递状态和客户端可见性。无批准调用路径时停止该子测试并记 `BLOCKED`，不要因 SDK 方法存在跳过入口核验。

**禁止动作**：调用旧三参数 `reply()` 充当证据、把被动字段放进主动 target、仅凭 SDK 方法宣称文件或图片可用、创建 ad hoc 脚本或直接调用 provider 来触发文件/图片发送。

**停止条件**：错误收件人、重复发送、投递歧义或敏感内容外发。

### Phase 13：失败、重试与歧义

**前置条件**：Phase 3 已建立失败任务基线；只处理业务影响已理解、对象 owner 明确且不存在外部歧义的任务。

**例行基线检查**

```bash
cd "$APP_DIR"
```

先按第 3.4 节记录 `/health` 聚合基线。若计数非零，只从批准的预脱敏 metadata source 或只读聚合证据操作员取得允许的 kind/state 明细。例行证据不得运行、复制或 shell-filter 原始 `failures --include-stale`。

仓库只支持以下五种 retry kind：`outbox`、`notification`、`pending_action`、`knowledge_index`、`knowledge_cleanup`。只能对匹配 kind 和允许状态的现有失败记录重排。

**批准重试的私有终端流程**

重试必须分配一名 dedicated task owner，并使用批准的私有服务器终端。必须关闭 session recording、截图和 shell xtrace；不得把终端共享到聊天或会议录制。完整 ID 通过 no-echo `read -r -s` 输入变量，不能出现在 shell history：

dedicated owner 只能从批准的、排除错误正文和业务内容的私有 ID-only 任务来源取得精确 ID。不得用原始 failure 输出寻找 ID，因为其中的 `last_error` 可能包含不可显示内容。无法安全取得精确 ID 时，不进行重试并记为 `BLOCKED`。

```bash
cd "$APP_DIR"
set +x
set -o pipefail
TASK_KIND='<approved-supported-kind>'
IFS= read -r -s -p 'Exact task ID: ' TASK_ID
printf '\n'
trap 'unset TASK_ID TASK_KIND RETRY_STATUS' EXIT HUP INT TERM
uv run python -m jobs_status_manager retry "$TASK_KIND" "$TASK_ID" >/dev/null 2>&1
RETRY_STATUS=$?
unset TASK_ID
unset TASK_KIND
printf 'retry_exit=%s\n' "$RETRY_STATUS"
unset RETRY_STATUS
trap - EXIT HUP INT TERM
```

retry 命令的原始 stdout/stderr 在到达终端前全部丢弃，只显示退出码。立即 `unset` 后，通过第 3.4 节批准的预脱敏 metadata source 或只读聚合证据操作员核对状态变化，不运行或过滤原始 failure CLI。若没有可用于后验核对的安全来源，retry verification 记为 `BLOCKED`。不得为了查看错误而重跑不带重定向的命令；需要内容诊断时由 owner 提供批准的 sanitized summary。若无法保证 dedicated owner、私有非记录终端、safe exact-ID source、no-echo 输入、变量调用、立即 `unset` 和 output suppression/no-copy，retry testing 记为 `BLOCKED`。

AgentRun 只能通过批准的预脱敏 metadata source 或只读聚合证据查看状态，没有安全手工 retry 命令，严禁手工重试。外部投递结果歧义时严禁盲重试。

当前 notification dispatch 可能把抛出的 `TimeoutError` 映射为 `RETRY_WAIT`。因此禁止在线注入 timeout 或传输中断。真实 notification timeout 子项在未通过隔离环境证明自动重试已关闭前必须记为 `BLOCKED`。

**PASS 标准**：health 聚合基线完整；非零明细来自批准的安全聚合来源；批准的五类重试使用私有流程，只重排匹配记录，不绕过确认，不创建新业务事实，并通过安全来源完成后验核对。聚合计数为零时 baseline 可 `PASS`，重试子项保持 `NOT_RUN`。

**FAIL 标准**：重试错误 kind、绕过确认、手工 AgentRun retry、歧义后重试、在线 timeout 注入、重复外部效果或宣称 exactly-once。

**BLOCKED 标准**：health 计数非零但无安全明细来源、任务影响未知、dedicated owner 不明确、有歧义、没有隔离能力、无法证明 notification 自动重试关闭、不能保证私有非记录终端和完整的 ID/output 安全流程，或重试后无安全核验来源。

**证据**：health 的 `durable_tasks`、`failed_tasks`、`stale_tasks`；安全来源提供的 kind、state、attempts、stale、可选 next_retry 和有界计数；retry 退出码、业务事实增量、QQ/HTTP 错误分类。不记录完整 ID、原始 CLI 输出、错误正文或数据库行。

**首层排查**：先 task kind 和持久状态，再错误分类和外部副作用，再判断是否属于五类安全 retry。遇到歧义直接冻结，不进入 retry。

**禁止动作**：手工重试 AgentRun、跨 kind retry、歧义后盲重试、在线注入 timeout 或传输中断。

**停止条件**：任何歧义、AgentRun 手工重试建议、在线故障注入或重复发送。

### Phase 14：SIGTERM 与一次重启恢复

**前置条件**：维护窗口书面批准；没有未决歧义；Phase 13 已清楚记录持久任务；可保证同一制品、环境、工作目录和数据库；只允许一次重启。

**动作**

1. 记录当前唯一 PID、启动时间、health、第 3.4 节安全聚合基线和关键对象聚合基线。
2. 由批准 supervisor 向唯一进程发送 SIGTERM。不要使用 SIGKILL 作为正常验证。
3. 确认 workers 先停，随后 botpy client、transport 和数据库资源各关闭一次。
4. 由批准 supervisor 使用同一制品、环境、工作目录和数据库启动一次。
5. 重复 Phase 1 和 Phase 2 的只读检查，并核对 stale Notification、PendingAction、AgentRun 和 Knowledge 恢复结果。
6. 核对没有重复业务事实或重复外部发送。

**PASS 标准**：有序退出和一次启动完成，仍只有一个进程和监听器，readiness 恢复，持久工作可恢复或明确失败，没有重复事实或发送。

**FAIL 标准**：资源重复关闭异常、启动到不同数据库、出现第二进程、恢复后重复事实或重复发送、ready 未恢复且原因属于实现问题。

**BLOCKED 标准**：没有维护批准、有未决歧义、不能保证同一运行身份、无法控制只重启一次。

**证据**：前后 PID 脱敏值、退出和启动 UTC、制品/环境/工作目录/数据库别名一致性、health、安全持久任务聚合摘要、对象和发送增量。

**首层排查**：先 supervisor 的 SIGTERM 转发，再关闭顺序，再启动身份，再 startup recovery，最后检查重复。不要通过连续重启“等它变好”。

**禁止动作**：未获维护批准发送 SIGTERM、使用 SIGKILL 代替正常验证、连续重启、改变制品或数据库。

**停止条件**：歧义未解决、第二进程、身份漂移、重复事实或重复发送。

### Phase 15：最终备份、完整性、隔离恢复检查与清理

**前置条件**：所有已执行步骤都有记录，外部副作用明确，无进行中操作，最终私有备份路径已批准且目标不存在。

**安全命令**

```bash
cd "$APP_DIR"
uv run python -m jobs_status_manager integrity-check
uv run python -m jobs_status_manager backup './backups/jobs-<RUN_ID>-final.db'
uv run python -m jobs_status_manager restore-check './backups/jobs-<RUN_ID>-final.db'
sudo ss -ltnp
```

最终持久任务摘要按第 3.4 节记录 health 聚合计数；需要明细时只使用批准的预脱敏 metadata source 或批准的只读聚合证据，不运行、复制或 shell-filter 原始 failure 输出。

**清理动作**

1. 按批准清单清理测试 QQ 消息、邮件、上传、fixture、临时证据和 `.part` 残留。不要宽泛删除线上目录。
2. 按批准策略恢复测试 Application，仍须走确认路径。
3. 检查持久任务、额外监听器、额外进程、未知测试制品和证据敏感模式。
4. 只保留批准的备份、审计记录和一个正常服务进程。

**PASS 标准**：integrity 和隔离 restore-check 成功；最终备份目标正确；无 `.part`、未知任务、额外监听器、额外进程、测试附件或敏感证据；所有清理 owner 签字。

**FAIL 标准**：备份覆盖、恢复替换线上数据库、完整性失败、清理失败、未知残留或秘密残留。

**BLOCKED 标准**：外部副作用仍不明确、清理 owner 缺席、不能确认备份隔离或仍有任务运行。

**证据**：命令和退出码、schema、integrity、备份别名和权限摘要、残留有界计数、监听器数、证据 scrub 结果、清理签字。

**首层排查**：先冻结清理对象，确认 owner 和范围，再处理明确的单项残留。不要执行递归删除整个 data、uploads、Chroma 或 backups 目录。

**禁止动作**：宽泛删除线上目录、覆盖备份、用 restore-check 替换线上数据库、把 SQLite 备份写成完整应用恢复证明。

**停止条件**：任何未知残留、失败清理、错误数据库或敏感泄漏都阻止最终签字。

SQLite backup 只覆盖 SQLite 数据库。它不包含 uploads 或 Chroma，也不能证明完整应用数据已备份或可恢复。uploads 需要独立批准的备份和恢复策略；Chroma 是可重建索引，但重建只允许在停止或静默状态执行。

## 6. 按症状定位第一失败层

通用规则：先写下“第一个与预期不符的层”，后续连锁症状不要各自当成新根因。每次只使用有界命令和 UTC 窗口。安全下一步由对应 owner 执行，禁止动作持续有效。

### systemd inactive 或重启循环

- **第一失败层**：supervisor 或进程启动层。
- **有界检查**：`systemctl show` 的六个批准字段；`systemctl status --no-pager --lines=0`；批准的预脱敏日志视图或日志 owner 针对 UTC 窗口制作的 sanitized summary。若只需数量，可用第 3.3 节的 metadata-only 行数管道。
- **安全下一步**：核对 unit 是否加载、工作目录、制品、依赖文件权限和安全错误分类，由部署 owner 修复后只启动一次。
- **禁止动作**：反复 `restart`、启动第二实例、打印 Environment 或完整 ExecStart 参数。
- **分类**：已批准服务启动失败为 `FAIL`；服务名或 supervisor 未确认时为 `BLOCKED`。

### 监听器是 `127.0.0.1`，但 Docker upstream 需要宿主机接口

- **第一失败层**：监听 bind 与代理拓扑不匹配。
- **有界检查**：`ss -ltnp`、批准的容器 network mode、容器内名称解析和内部 `/live`。
- **安全下一步**：由平台 owner 选择批准拓扑，例如受防火墙保护的 `0.0.0.0`、host network 或其他正式方案，再走变更审批。
- **禁止动作**：临时把 8000 公网开放，或在容器和宿主机各启动一个应用。
- **分类**：实际配置与批准拓扑不符为 `FAIL`；拓扑未决定为 `BLOCKED`。

### 本地 connection refused

- **第一失败层**：进程或监听器。
- **有界检查**：Phase 1 的 `systemctl show`、`ps`、`ss`，然后使用批准的 sanitized log source/window。
- **安全下一步**：确认探针地址和端口与批准 bind 一致；若进程已死，由 supervisor owner 处理一次启动。
- **禁止动作**：先改 NPM、先查 QQ、启动第二监听器。
- **分类**：批准监听器未工作为 `FAIL`；端口尚未批准为 `BLOCKED`。

### 公网 502 或 504

- **第一失败层**：NPM upstream，除非 Phase 4 已证明 upstream 正常，此时再看代理路由或超时。
- **有界检查**：公网 `curl`、bounded `docker ps`、容器内解析和内部 `/live`，以及批准的预脱敏容器日志视图或 owner sanitized summary。只需日志数量时使用第 3.3 节 metadata-only 管道。
- **安全下一步**：比较本地、容器内和公网三层结果，修复第一个失败跳点。
- **禁止动作**：把 502/504 记为 PASS，暴露 8000，或重启多个组件掩盖根因。
- **分类**：入口已批准且 upstream 失败为 `FAIL`；代理维护或路由未批准为 `BLOCKED`。

### NPM 无法解析宿主机或 upstream 失败

- **第一失败层**：容器 DNS/网络或宿主机 bind。
- **有界检查**：`docker exec ... getent hosts host.docker.internal`，bounded `docker inspect`，容器内内部 HTTP 探针，宿主机 `ss`。
- **安全下一步**：确认候选 `host.docker.internal:8000` 是否已正式批准，并按实际 Docker 拓扑修复名称或路由。
- **禁止动作**：写死未知宿主机 IP、打印完整容器配置、临时安装不受控工具。
- **分类**：批准拓扑不工作为 `FAIL`；候选值未审批为 `BLOCKED`。

### `/health` 503，`database=unavailable`

- **第一失败层**：SQLite 可读性。
- **有界检查**：CLI `health`、`integrity-check`、工作目录和数据库别名、批准的 sanitized log source/window。
- **安全下一步**：核对路径解析、owner 权限、文件存在性、锁和磁盘状态，再由数据库 owner 修复。
- **禁止动作**：创建新数据库顶替、启动第二进程、输出数据库内容。
- **分类**：批准数据库不可读为 `FAIL`；数据库目标未确认为 `BLOCKED`。

### `/health` 503，`database=not_ready` 或 schema mismatch

- **第一失败层**：schema。
- **有界检查**：CLI `health`；带明确批准 `APP_DATABASE_PATH` 的 `alembic current` 和 `alembic check`。
- **安全下一步**：确认正在检查同一数据库，停止当前 Run。由 deployment owner 在独立批准的 change procedure、pre-change backup 和 recovery plan 下处理，完成后使用新 Run ID 重跑。
- **禁止动作**：在当前验证 Run 中运行 `migrate` 或 `bootstrap`、省略 `APP_DATABASE_PATH` 直接跑 Alembic、创建旁路 DB、手改 schema 表。
- **分类**：批准目标上的 schema 非 head 为 `FAIL`；路径无法确认为 `BLOCKED`。

### `/health` 503，`product_readiness=not_ready`

- **第一失败层**：已启用能力的配置或 adapter 构造。
- **有界检查**：health 的能力状态、批准的启动日志 sanitized summary、能力快照和批准主机名。
- **安全下一步**：由对应能力 owner 修复配置配对和 adapter wiring，再按单进程策略启动一次。
- **禁止动作**：打印环境值、用 fake adapter 宣称 provider ready、改成 local-only 掩盖生产配置失败。
- **分类**：已批准启用能力构造失败为 `FAIL`；能力未配置则相关外部用例为 `BLOCKED`。

### `/health` 200，`durable_tasks=degraded`

- **第一失败层**：持久任务，不是进程层。
- **有界检查**：先记录 `/health` 的 `durable_tasks`、`failed_tasks` 和 `stale_tasks`。计数非零时，仅从第 3.4 节批准的预脱敏 metadata source 或只读聚合证据取得 kind、state、attempts、stale、可选 next_retry 和有界计数。
- **安全下一步**：按 Phase 13 判断五类安全重试或交接 AgentRun/歧义任务。
- **禁止动作**：仅因 degraded 重启服务，盲重试，声称进程死亡。
- **分类**：health 可保持 `PASS`，相关 durable task 用例按实际记 `FAIL`、`BLOCKED` 或 `NOT_RUN`。

### CLI、systemd 与 Alembic 指向不同数据库

- **第一失败层**：环境注入或工作目录导致的数据库目标错配。
- **有界检查**：身份表数据库别名、systemd WorkingDirectory、CLI 工作目录、显式批准的 Alembic 路径。不要打印环境。
- **安全下一步**：冻结写入，找出哪个路径偏离批准目标，由 Lead 书面指定唯一目标。
- **禁止动作**：合并数据库、复制行、继续迁移或继续 provider 测试。
- **分类**：已执行到错误数据库为 `FAIL` 并全局停止；尚未确定唯一目标为 `BLOCKED`。

### QQ 返回 400、401、403 或 503

- **第一失败层**：先按状态分类。400 为请求契约或路由，401 为 token/凭据，403 为账号权限或 capability，503 为 provider 服务或批准端点可用性。
- **有界检查**：HTTP 状态、QQ `err_code`、批准 API/Token 主机、脱敏 trace、批准的 sanitized log source/window。
- **安全下一步**：由 QQ owner 核对官方控制台和批准配置，不复制请求 body 或 Authorization。
- **禁止动作**：轮换或粘贴秘密到 shell、切换到未批准端点、反复发送。
- **分类**：能力已批准且请求实际失败为 `FAIL`；权限、凭据或 provider 窗口缺失为 `BLOCKED`。

### `op=13` 失败

- **第一失败层**：回调 DNS/TLS/NPM/路由或 challenge transport，按最早失败点填写。
- **有界检查**：公网 `/live`、批准 callback 主机和路径、平台状态、HTTP 状态、脱敏 trace。
- **安全下一步**：先确认真实路由来自部署配置，再从公网入口逐层到 transport。
- **禁止动作**：记录 challenge token、签名或原始 payload，猜测 `/qq/callback` 就是实际路由。
- **分类**：已批准 challenge 执行失败为 `FAIL`；平台未开放验证窗口为 `BLOCKED`。

### 已 ACK，但没有回复

- **第一失败层**：若入站已持久化且 ACK 正确，则依次检查 AgentRun、LLM、ReplyTarget、Notification/Outbox 和 provider 投递。
- **有界检查**：第 3.4 节的 health/安全聚合基线、批准的只读聚合状态、sanitized log source/window、QQ `err_code`。
- **安全下一步**：找到第一项未进入预期状态的 durable 对象，交给对应 owner。
- **禁止动作**：重放原消息、手工 retry AgentRun、直接主动推送来掩盖被动失败。
- **分类**：已启用链路的实现失败为 `FAIL`；LLM 或发送能力禁用为 `BLOCKED`。

### 出现重复记录

- **第一失败层**：入站幂等、UID/consumer 幂等、确认幂等或重启恢复中的最早重复点。
- **有界检查**：由只读证据操作员报告相关对象增量、状态和相同脱敏 hash。
- **安全下一步**：冻结所有依赖写入和发送，保留现状供根因分析。
- **禁止动作**：删除生产行、再重放、再确认、再重启。
- **分类**：始终为 `FAIL`，并触发全局停止。

### 状态确认未创建或未执行

- **第一失败层**：未创建时查 LLM/tool proposal；已创建未执行时查确认解析、PendingAction 状态和 worker。
- **有界检查**：AgentRun 和 PendingAction 状态、工具名、有界计数、第 3.4 节批准的安全聚合来源。
- **安全下一步**：核对用户动作对应同一 proposal 的脱敏 hash，按 normal lifecycle 恢复。
- **禁止动作**：直接写 Application、伪造确认、手工重试 AgentRun。
- **分类**：已批准链路行为不符为 `FAIL`；LLM 或测试 Application 不可用为 `BLOCKED`。

### IMAP 认证失败、无新邮件或 cursor 错误

- **第一失败层**：认证失败在 IMAP auth/TLS；无新邮件先查基线和 UID 搜索；只有 gateway poll 或 durable mail ingestion 完成前的高水位异常属于 cursor 层。ingestion 后的 consumer、LLM、proposal 或 notification 错误属于各自 durable processing 层。
- **有界检查**：连接结果分类、poll 次数、reset 布尔值、cursor hash、邮件 durable ingestion 完成布尔值、邮件新增计数、批准的 sanitized log source/window。
- **安全下一步**：确认初次 poll 故意以当前 max UID 建基线。认证由邮箱 owner 修复私有配置；ingestion 前 cursor 异常时冻结 poll。若 ingestion 已成功且 cursor 已推进，保持 cursor 不变，转查下游 durable task/event 状态。
- **禁止动作**：记录地址、密码、正文或完整 cursor；为了找邮件、重跑下游或补偿 downstream failure 而重置或回退线上 cursor。
- **分类**：已批准账号认证失败，或 gateway/ingestion 前 cursor 错误为 `FAIL`；账号或发件通道不可用为 `BLOCKED`；正确基线后的 0 封历史邮件可为 `PASS`；ingestion 后 cursor 正常推进且下游失败，不记为 cursor `FAIL`。

### 附件 4xx、MIME、大小或 SSRF 拒绝

- **第一失败层**：4xx 按 URL/下载契约；MIME 不符在内容类型校验；超限在大小边界；私网目标在 SSRF 防护。
- **有界检查**：HTTP 状态、错误分类、扩展名、MIME 类别、字节区间、`.part` 残留计数。
- **安全下一步**：确认负例是否预期安全拒绝。正例失败时由文件 owner 核对批准 fixture 和 provider metadata。
- **禁止动作**：关闭 SSRF/MIME/大小/timeout/deadline、复制附件 URL 或内容、手工下载到公共目录。
- **分类**：负例按预期拒绝可 `PASS`；批准正例错误拒绝为 `FAIL`；文件能力禁用为 `BLOCKED`。

### 文件或图片出站没有批准调用路径

- **第一失败层**：operator invocation surface，不是 adapter 方法本身。
- **有界检查**：部署流程清单、能力快照和业务 owner 的书面批准，确认是否存在会自然到达生产 `send_file` 或 `send_image` adapter 的已部署业务流。
- **安全下一步**：存在批准业务流时按 Phase 12 执行；不存在时停止对应子测试并记录缺失入口。
- **禁止动作**：创建 ad hoc Python 或 shell 脚本、调用未记录 HTTP endpoint、直接调用 provider、把方法存在性当作可执行入口。
- **分类**：没有已部署且批准的调用路径时为 `BLOCKED`；不得为了消除阻塞而临时发明入口。

### Knowledge 长期 PENDING、FAILED 或搜索无命中

- **第一失败层**：依次为 worker 调度、embedding、Chroma 写入、文档资格过滤或搜索。
- **有界检查**：Knowledge 状态、attempt、chunk 计数、第 3.4 节批准的安全聚合来源、命中数和分数区间。
- **安全下一步**：确认 SQLite 文档和 chunk 是事实源；对匹配失败任务使用批准的 `knowledge_index` 或 `knowledge_cleanup` retry。只有停机或静默时考虑 rebuild。
- **禁止动作**：运行态 `rebuild-chroma`、把 Chroma 当事实源、输出 chunk 正文。
- **分类**：启用能力的处理失败为 `FAIL`；embedding/Chroma 禁用为 `BLOCKED`。

### provider 4xx、408、425、429、5xx 或 timeout

- **第一失败层**：4xx 为请求/权限类；408、425、429 和部分 5xx 可能是 retryable；timeout 可能歧义，必须按实际持久分类处理。
- **有界检查**：状态码、QQ `err_code`、attempt、next_retry、持久状态、脱敏 provider ID。
- **安全下一步**：只有仓库已持久化为明确 retryable 且对象属于五类安全 retry 时，才由 Lead 批准处理。permanent 错误不重试。
- **禁止动作**：在线注入 timeout、把所有 4xx/5xx 一概重试、在歧义时重发、宣称 exactly-once。
- **分类**：已执行的明确失败为 `FAIL`；provider 未批准或 timeout 安全条件不满足为 `BLOCKED`。

### 外部投递结果歧义

- **第一失败层**：delivery ambiguity。
- **有界检查**：持久状态、attempt、客户端可见布尔值、provider 状态和脱敏 ID。不要再次调用 provider 探测。
- **安全下一步**：冻结发送、重试、重放和重启；由 Lead 与收件人 owner 人工核对一次，写交接记录。
- **禁止动作**：盲重试、重启、发送补偿消息、修改状态为成功。
- **分类**：安全能力缺失时测试项 `BLOCKED`；实际出现未受控歧义时当前项 `FAIL` 并全局停止。

### stale durable tasks

- **第一失败层**：对应 Notification `SENDING`、PendingAction `EXECUTING`、AgentRun `RUNNING` 或 Knowledge `INDEXING` 生命周期。
- **有界检查**：先记录 `/health` 的 durable/failed/stale 聚合字段。需要 kind、state、attempts、stale 或 next_retry 时，只使用第 3.4 节批准的预脱敏 metadata source 或只读聚合证据。
- **安全下一步**：先确认启动恢复是否运行，再按 task kind 交给 owner。AgentRun 只通过正常 lifecycle 恢复。
- **禁止动作**：因为 stale 直接重启循环，手工 retry AgentRun，跨 kind retry。
- **分类**：health 可保持 200；任务本身按实现结果记 `FAIL`，有歧义则 `BLOCKED` 后续动作。

### 重启后出现重复

- **第一失败层**：startup recovery 或 at-least-once 投递边界。
- **有界检查**：重启前后对象增量、发送增量、脱敏幂等键、一次启动时间线。
- **安全下一步**：立即冻结所有写入和外部发送，保存有界状态供恢复 owner 分析。
- **禁止动作**：再次重启、删除重复行、再次确认或发送。
- **分类**：重复业务事实或发送均为 `FAIL` 并触发全局停止。

## 7. 可复用单用例记录模板

每个手工步骤复制一份。未执行字段保持占位，不得填历史结果。

```markdown
### <CASE-ID> <简短名称>

| 字段 | 记录 |
|---|---|
| Status | <PASS / FAIL / BLOCKED / NOT_RUN> |
| Run ID | <RUN_ID> |
| UTC | <start/end ISO-8601 UTC> |
| Owner | <owner-alias> |
| Artifact | <artifact-id-or-commit> |
| Environment aliases | <host/runtime/supervisor/workdir aliases> |
| Capability aliases | <QQ/LLM/IMAP/embedding/Chroma/inbound-file/outbound-file/image states> |
| Dependency | <case IDs and statuses> |
| Command or action | <exact safe command or approved semantic action label> |
| Exit / HTTP / QQ status | <exit code, HTTP status, QQ err_code> |
| Schema | <observed schema and approved expected schema> |
| Expected | <bounded expected result> |
| Actual bounded observation | <counts, states, booleans, no bodies> |
| Redacted ID | <hash prefix or truncated ID> |
| Durable side effects | <bounded object deltas and state transitions> |
| Cleanup | <action, owner, result> |
| First failed layer | <DNS/TLS/NPM/process/listener/config/DB/schema/QQ/LLM/IMAP/file/knowledge/delivery/recovery/cleanup/none> |
| Sanitized log source/window | <source alias and UTC window, no sensitive body> |
| Next owner | <owner-alias or none> |
| Prohibited next action | <specific action that must not occur> |
| Handoff question | <one concrete question or none> |
```

`BLOCKED` 必须写清缺少的能力或审批，不执行试探性 provider 调用。`FAIL` 只记录第一失败层。实际结果只写有界观察，不复制日志正文。

## 8. 全局停止条件与冻结流程

出现以下任一情况，立即冻结外部动作：

- 收件人、账号、endpoint、callback 主机或路径与批准值不一致。
- 任何敏感信息泄漏到 shell 历史、终端、截图、证据或日志摘录。
- 普通 QQ ACK 早于持久化。
- event/message、UID、consumer、确认或重启产生重复 durable fact 或重复发送。
- 下载绕过 HTTPS、SSRF、MIME、大小、timeout、deadline 或私有存储边界。
- 外部副作用歧义。
- 实际数据库或 schema 不符合身份表。
- 已批准部署缺少预期 bootstrap identity，或有人计划在当前 Run 中就地运行 `migrate`/`bootstrap`。
- 出现第二进程、第二监听器或第二 botpy client。
- 清理失败或存在未知残留。
- 任何在线 timeout 或传输中断注入计划或动作。
- 原始日志正文、原始 failure 输出或未抑制的 retry 输出将被显示到终端。

冻结后必须完成以下步骤：

1. Lead 停止新的 provider 调用、发送、重试、重放、重启和写操作。
2. 当前步骤按已观察事实记 `FAIL` 或 `BLOCKED`。未开始的依赖项记 `NOT_RUN`；确认能力缺失的相关项记 `BLOCKED`。
3. 向所有操作员传播冻结状态、Run ID、触发用例和禁止动作。
4. 创建交接记录，包含停止 UTC、第一失败层、已知有界副作用、脱敏日志源和窗口、下一 owner、单一待回答问题。
5. 只有 Lead 收到书面 release，确认歧义和安全风险已解除后，才能恢复。口头同意或单个代理自行判断不够。

## 9. 最终收尾清单

### 运行与证据收尾

- [ ] 31 项台账中每一项都有 `PASS`、`FAIL`、`BLOCKED` 或 `NOT_RUN`，四类合计为 31。
- [ ] 每个已执行项有 Run ID、UTC、owner、artifact、前置状态、第一失败层、证据和清理记录。
- [ ] 所有失败和阻塞都有下一 owner、安全下一步和禁止动作。
- [ ] 没有未决外部投递歧义。
- [ ] 只有一个批准应用进程、一个 SQLite 目标、一个 Starlette 监听器和一个 botpy client。
- [ ] `/live` 与 `/health` 的本地和公网结论按各自语义记录，未把 health 当作 QQ 证明。
- [ ] 预备份和最终 SQLite 备份目标不同、此前不存在、权限私有，隔离 restore-check 已记录。
- [ ] 已明确写出 SQLite 备份不包含 uploads 或 Chroma，不是完整应用数据恢复证明。
- [ ] 测试消息、邮件、附件、fixture、`.part`、临时证据和批准清理项已逐项处理，没有宽泛删除线上目录。
- [ ] 第 3.4 节 health/安全聚合基线、监听器和残留计数已复核；未运行、复制或 shell-filter 原始 `failures --include-stale`，也未记录完整 ID、错误或数据库行。
- [ ] 日志内容只来自批准的预脱敏视图或 owner sanitized summary；没有先显示原始日志再补做脱敏。
- [ ] 证据经过人工脱敏复核，没有秘密、完整 ID、payload、正文、地址、URL 查询串、数据库行或备份内容。
- [ ] 签字只确认程序按记录执行，不声明生产就绪、provider 全兼容或 exactly-once。

### 文档与仓库核验命令

以下命令由代码维护者在仓库工作目录执行。它们是文档和仓库检查，不是实时 provider、部署成功或生产就绪证据。Alembic 必须指向一个已批准、已迁移的测试数据库，不能省略路径，也不能指向线上数据库。

```bash
git diff --check
git diff -- docs/deployed-manual-validation-troubleshooting.zh-CN.md
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv lock --check
APP_DATABASE_PATH='<approved-migrated-test-db-path>' uv run alembic current
APP_DATABASE_PATH='<approved-migrated-test-db-path>' uv run alembic check
```

最后由人工检查目标文件和 targeted diff，搜索凭据、`Authorization`、secret、token、signature、challenge 值、完整 openid、完整 event/message/provider ID、邮件地址、正文、prompt/response、附件 URL/query/content、私钥、数据库内容和备份内容。自动搜索只能帮助定位，不能替代逐段审阅。

本手册的创建不执行任何部署、服务、容器、provider、数据库、测试或 git 命令，也不改变 31 项台账的状态。
