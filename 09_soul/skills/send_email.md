# Skill: Send Email via Gmail

This skill allows the AI to send email notifications using a Gmail app-specific password.

## When to Use
- 发送报告通知
- 发送任务完成提醒
- 发送重要信息摘要

## Prerequisites
- A `.env` file in the root directory containing:
  - `GMAIL_USERNAME`: The Gmail address used to send the email.
  - `GMAIL_APP_PASSWORD`: A Gmail app-specific password.
  - `GMAIL_RECIPIENTS`: The default recipient address (e.g., `<your-email>`).

## Gmail App Password Setup

1. Go to [Google Account](https://myaccount.google.com/) → Security → 2-Step Verification
2. At the bottom: App passwords → Generate a new app password
3. Add to `.env`:
   ```
   GMAIL_USERNAME=your.gmail@gmail.com
   GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
   GMAIL_RECIPIENTS=<your-email>
   ```

## Usage

### Basic usage (plain text)
```bash
python3 09_soul/tools/send_email_to_myself.py "Subject Here" "Body Here"
```

### Send to a specific address
```bash
python3 09_soul/tools/send_email_to_myself.py "Subject Here" "Body Here" --to <recipient-email>
```

### Send HTML content
```bash
python3 09_soul/tools/send_email_to_myself.py "Subject Here" "<h1>HTML Body</h1>" --html
```

### Send from file (Markdown auto-converted to HTML)
```bash
python3 09_soul/tools/send_email_to_myself.py "Report Title" "" --file path/to/report.md
```

### Send from file with custom CSS
```bash
python3 09_soul/tools/send_email_to_myself.py "Report Title" "" --file path/to/report.md --css path/to/styles.css
```

## Markdown to HTML Conversion

当使用 `--file` 参数指定 `.md` 文件时，脚本会自动将 Markdown 转换为 HTML：
- 标题（#、##、###）→ h1、h2、h3
- **粗体**、*斜体*
- [链接](url)
- 列表（- 和 1.）
- 表格（| ... |）
- 代码（`code`）
- 分隔线（---）

内置 GitHub 风格 CSS，可通过 `--css` 参数自定义。

## Example

Send a markdown report as styled HTML email:
```bash
python3 09_soul/tools/send_email_to_myself.py "Weekly AI Report" "" --file 09_soul/contexts/survey_sessions/ai_news_weekly.md
```
