# Spark Program Workbench（本地浏览器版）

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
- Nervos Talk 正式发帖只支持成员自己的 Discourse User API Key，不保存论坛密码。
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

团队部署前，必须放在带 HTTPS 和组织身份认证的私有服务后面。不要把本服务直接绑定到 `0.0.0.0`。

创建工作台用户：

```powershell
python -m spark_web.manage_users alice --role operator --forum-username alice_on_forum
```

如果该成员需要以自己的身份正式发帖，将其个人 Discourse User API Key 写入 `.env`：

```dotenv
SPARK_DISCOURSE_USER_API_KEY_ALICE_ON_FORUM=
```

草稿可以由团队协作准备，但正式发帖请求必须由绑定该论坛身份的工作台用户本人确认“正式发布”。

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

不要同时运行旧 Tkinter 版和浏览器版修改任务数据。浏览器版启用后，应以它作为唯一写入端。
