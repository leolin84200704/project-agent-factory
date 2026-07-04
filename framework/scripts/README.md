# scripts — dream pipeline 樣板

這些是 **template copies**，不直接執行。初始化新 instance 時（見 `../../BOOTSTRAP.md`）複製到 instance 的 `scripts/` 後修改：

- 路徑：memory 根目錄改成 instance repo
- Ticket 系統：`reconcile-jira.py` 是 Jira 版範例，其他系統照它的 reconcile 模式重寫
- 排程：`install-launchd.sh` 的 label 與時間改成 instance 專屬

目前的活體（Vibrant America instance）在 `~/src/lis-code-agent/scripts/`，launchd 每晚 6:30 PM 跑 — 那份才是會演化的版本；框架大改時再回填這裡。
