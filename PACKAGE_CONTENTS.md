# 覆盖升级包内容

本包包含浏览器完整版服务、原桌面程序、启动脚本、Notion 配置与验证、自动化工作流、测试和使用说明。

为保护本机数据，包内刻意不包含：`.env`、`spark_task_data.json`、`spark_workbench.db`、`spark_project_cache.json`、`knowledge/`。解压覆盖不会删除现有文件，因此这些数据会原样保留。

主要入口：

- `Launch-Web.vbs`：启动浏览器完整版。
- `Setup-Notion.bat`：首次验证并保存 Notion Personal Access Token。
- `UPGRADE_BROWSER_V2.md`：从当前版本覆盖升级的步骤与刷新修复说明。
- `README_BROWSER.md`：完整功能和团队模式说明。
