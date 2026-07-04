---
name: ticket-attachment-to-md
description: Convert a Jira/ticket attachment to readable text. Use whenever a ticket has a downloaded attachment you need the content of — PDF, Excel (.xlsx/.xls), CSV, Word (.docx), or PowerPoint (.pptx) go through markitdown; image attachments (PNG/JPG/JPEG) are read directly. Trigger on "把這附件/PDF/excel 轉一下", "讀這張圖", or when a ticket references an attached file.
---

# Ticket Attachment → Markdown / Text

Route by file type. Do NOT use markitdown on images — it only returns metadata; Claude reads images natively and far better.

## Routing

| 副檔名 | 做法 |
|---|---|
| `.pdf` | `markitdown` → markdown text. (掃描型/影像 PDF 轉出空白時，改用 Read tool 的 `pages` 參數讓 Claude 直接看) |
| `.xlsx` `.xls` `.csv` | `markitdown` → markdown 表格 |
| `.docx` `.pptx` | `markitdown` → markdown |
| `.png` `.jpg` `.jpeg` | **直接用 Read tool 讀**（Claude 原生視覺）。markitdown 對截圖只給 EXIF，不要用 |

## markitdown 指令

binary 在 pipx venv，PATH 可能沒它，用絕對路徑最穩：

```bash
~/.local/bin/markitdown "<檔案路徑>" -o "<輸出>.md"
# 或直接 stdout:
~/.local/bin/markitdown "<檔案路徑>"
```

轉完用 Read 讀那份 `.md` 再分析。需要時把輸出放到 scratchpad 目錄，別丟進使用者專案。

## 注意
- markitdown 0.1.6, pipx 裝, Python 3.14。已含 pdf/docx/pptx/xlsx/xls extras。
- 圖片走 Read、其餘走 markitdown — 別搞反。
- 這是 read-only 轉換，產出放 scratchpad，不動 ticket 原檔。
