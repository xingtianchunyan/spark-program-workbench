# Spark Program Workbench（本地浏览器版）

审阅队列默认打开「仅草稿」。可直接编辑正文并点「保存修改」（Ctrl / ⌘ + S），也可选中文字后点「添加批注」；同事可从侧栏查看批注和定位原文。添加批注或标记已阅前会先保存当前修改。若其他成员已更新正文，旧版本不会覆盖新版本。正文和批注仅保存在工作台，不会发到 Talk。

## 现在已经实现的功能

- 浏览器任务页，默认只监听 `127.0.0.1`，不会暴露到局域网。
- 第一次启动时从 `spark_task_data.json` 导入现有任务，之后使用 SQLite 防止并发覆盖。
- 刷新 Nervos Talk 看板，检测新进入 `In-Progress`、`Completion`、`Closure` 的项目。
- 新项目自动提取并执行：资金分配、交易记录、进行中项目页和初始化模板。
- 已记录项目自动检测新回复，生成并追加每周更新。
- Completion / Closure 自动创建已完成项目页、复制页面内容、逐字段填写，并更新原项目与资金池状态。
- 所有创建操作先按 Talk Topic ID、提案 URL、已保存页面 ID 查重；标题仅作为唯一候选时的兼容回退。
- 只有创建后重新读取且字段一致，才会勾选“更新项目列表”和“更新资金池状态”。
- Dashboard 与联系人任务继续创建和保留；因尚无业务规则，不会假装完成。
- 团队模式提供工作台账号、角色、会话、CSRF 防护和审计记录。
- 四位委员会成员使用自己的 Nervos Talk 用户名和密码登录后即可审阅草稿；审阅和发布都不需要个人 API Key。正式发布需要有权限的成员再次明确确认并单次输入本人 Talk 密码。
- 浏览器版同时提供原工作台的任务、项目看板、时间线、项目跟踪、回复模板、AI 起草、常用链接、知识库、会议纪要和上会项目模块。
- 时间线的“项目跟踪”页以 Nervos Talk Topic ID 为稳定主键，合并导入项目字段与帖子阶段；每条委员会 Pending 回复都单独保留楼层与日期。默认显示 In-Progress，所有分类按最初发帖日期倒序。
- Notion 导出的 ZIP（包含子页面文件夹和 CSV）可直接拖入“项目跟踪”页，或点击“拖入 Notion ZIP 导出”选择文件；最多 50 MB。帖子时间线另有独立的“刷新帖子时间线”按钮，仅抓取论坛并更新本地缓存，不执行 Notion 同步。
- 可用“粘贴表格导入”导入从 Notion/Excel 复制的制表符表格、CSV 或 Markdown 表格，再点击“刷新在跑项目”从 Talk 补全阶段记录。资金表通过本机 Notion Token 只读同步：资金分配表的分配金额为通过项目的拟资助金额；前期提案仅从主帖识别申请预算，不从 Notion 取该列。In-Progress 的实际资助列原样显示进行中项目表的“已发放”（如原表为百分比，这里也显示百分比）；Completion/Closure 显示已完成项目表的“资助金额”，不会回退到总资助金额。打开项目跟踪时及页面保持打开期间每 5 分钟读取一次，亦可手动点“同步资金表（只读）”。读取失败或多行金额冲突时保留来源可信的原值；“约定更新”可选择每周星期，保存在本地，不会写回 Notion。
- 拟资助金额以资金分配表为先；没有匹配值时读取进行中项目表的“总资助金额”。点击帖子时间线中的提案行或看板项目卡片，可按帖子当前标签跳转到项目跟踪并定位 Topic；点击旁边 ↗ 仍打开论坛原帖。
- 无生命周期标签、由非委员会作者发布且被手动放进状态白名单的相关帖子，在项目跟踪中归为“申请人有话说”，保留白名单关联状态，不作为独立提案触发 Notion 自动写入。回复模板可新增或编辑，只有保存后才更新本地自定义模板。
- 刷新会先对账并认领手工维护的 Notion 行，再续跑旧的未完成步骤；对账失败时整轮停止写入。
- 手工非空字段优先，自动化只补空字段；重复候选和重复交易进入冲突状态，不会自动删除。

## 第一次使用

1. 安装 Python 3.11 或更新版本。
2. 在 Notion 的 Developer 页面创建启用了 **Notion API** 的 **Personal Access Token**。不要使用另一个工作区连接 token 或 OAuth Client Secret。
3. 双击 `Setup-Notion.bat`，粘贴 token。程序会检查四张目标表，并自动增加用于去重的 Topic ID/提案 URL 字段。
4. 双击 `Launch-Web.vbs`。浏览器将打开 `http://127.0.0.1:8765/`。
5. 点击“刷新并自动同步”。

### Windows 11 25H2 / Smart App Control

如果系统阻止 `.vbs` 或 `.bat`，不要为了本项目关闭整个 Smart App Control。优先双击
`Setup-Notion.pyw` 完成 Notion 设置，再双击 `Launch-Web.pyw` 启动。它们由已经安装的
Python 解释器执行；网页关闭后仍会自动释放端口。

如果电脑没有关联 `.pyw` 文件，请在项目根目录打开 Windows Terminal，依次运行：

```powershell
py -3 -m spark_web.setup
py -3 -m spark_web.server --auto-shutdown
```

第二条命令运行期间不要关闭终端窗口；工作台会自动打开浏览器。

从旧浏览器版升级时请先阅读 `UPGRADE_BROWSER_V2.md`；升级包不会覆盖 `.env`、任务 JSON、SQLite 数据库、看板缓存或知识库。

真实 token 只写入本机 `.env`，不要把 `.env` 发送给其他人。

## 自动提取

自动从论坛文字提取金额、钱包、交易和日期需要 `.env` 中已有以下任意一项：

- `SPARK_AI_API_KEY`
- `SPARK_KIMI_API_KEY`

字段缺失或相互冲突时，作业会停在 `needs_input` 或 `conflict`，不会写入猜测数据。可在“手动执行”页修正 JSON 后继续。

## 与 Notion MCP 的关系

项目保留了 `notion_mcp_config.toml.example`，可供 Codex 等交互式客户端连接官方 Notion MCP。
但 Notion 官方 MCP 目前要求交互式 OAuth，官方尚不支持无人值守授权。因此本地自动任务使用同一家公司提供的 Notion REST API；两者写入的是同一个复制版 Dashboard。

## 团队模式

本机单人使用时保持：

```dotenv
SPARK_TEAM_MODE=0
```

团队部署前，在同一主机使用私有 HTTPS 反向代理（工作台监听本机 `127.0.0.1`）。代理需保留浏览器的 Origin 并覆盖设置 `X-Forwarded-Proto: https`。将浏览器访问的网址（仅协议、域名和可选端口）填入 `SPARK_TEAM_HTTPS_ORIGIN`。不要把服务直接暴露在局域网 HTTP 或公网。

团队模式只接受这四个 Talk 账号：`xingtianchunyan`、`zz_tovarishch`、`Hanssen`、`yixiu.ckbfans.bit`。用户名不区分大小写，可带一个 `@`。先核对委员会名单，再由 Talk 校验密码；首次成功登录默认取得审阅权限。无需手动填写白名单（旧 `SPARK_TEAM_ALLOWED_TALK_USERS` 已不生效）。

```dotenv
SPARK_TEAM_MODE=1
SPARK_TEAM_HTTPS_ORIGIN=https://review.example.org
```

已明确绑定四个 Talk 账号之一的工作台用户保留原角色；其他账号不能通过旧的本地密码或旧会话进入。若论坛要求双因素验证，当前直登方式不支持，不应关闭论坛安全设置。

如需为委员会成员配置额外角色，管理员可显式绑定对应的 Talk 用户名：

```powershell
python -m spark_web.manage_users xingtianchunyan --role administrator --forum-username xingtianchunyan
```

上述命令仍会要求输入一个本地密码以维护数据库兼容性；请使用独立随机密码，不要输入 Talk 密码。浏览器团队登录只验证 Talk 密码。新版不使用旧明文密码；如旧 `spark_task_data.json` 存有 `nervos_password`，可停服后删除该字段并保留其余数据。

草稿审阅不会发帖。正式发布由绑定论坛身份且有发帖角色的成员单独确认，输入的论坛密码只用于该次请求。如果论坛要求双因素验证，可从确认窗口打开原帖，在 Talk 网页手动发布。

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

不要同时运行旧 Tkinter 版和浏览器版修改任务数据。浏览器版启用后，应以它作为唯一写入端。
