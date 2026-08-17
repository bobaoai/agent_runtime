# Skill: Delayed Execution (延时执行)

用于在指定时间后执行任务，支持两种模式：命令行任务和智能任务。

## When to Use
- 需要在一段时间后执行某个操作（如检查 DNS 传播、发送提醒）
- 定时任务但不想用 crontab

## 模式 1：命令行延时任务

适用于简单的命令行操作，用 `sleep` + 后台执行：

```bash
# 语法：(sleep <秒数> && <命令>) &
# 注意：整个命令用括号包起来放到后台，避免超时

# 示例：2小时后检查 DNS 并发邮件
(sleep 7200 && python3 09_soul/tools/<your-notify-script>.py "DNS Check Result" "$(dig TXT <your-domain> +short)") &

# 示例：1小时后执行某个脚本
(sleep 3600 && /path/to/script.sh) &

# 查看后台任务
jobs

# 注意：后台任务会在 shell 关闭后终止，如需持久化用 nohup
nohup bash -c "sleep 7200 && python3 09_soul/tools/<your-notify-script>.py 'Subject' 'Body'" &
```

## 模式 2：智能延时任务（OpenCode Agent）

适用于需要 AI 判断的复杂任务，通过 OpenCode API 提交：

```bash
# 使用 --no-wait 让任务异步执行
# 在 prompt 中明确说明"在 X 时间后执行"（需要外部触发）

# 实际延时需要配合模式 1 触发：
(sleep 7200 && python3 09_soul/tools/opencode_job.py "检查 DNS 记录 <your-domain> 和 <your-domain> 是否已传播完成，如果完成则发邮件通知 <your-email>" --title "DNS Check & Notify" --no-wait) &
```

## 时间换算

| 时间 | 秒数 |
|------|------|
| 1 分钟 | 60 |
| 5 分钟 | 300 |
| 10 分钟 | 600 |
| 30 分钟 | 1800 |
| 1 小时 | 3600 |
| 2 小时 | 7200 |
| 24 小时 | 86400 |

## Best Practices

### 始终使用日志重定向

**关键**：延时任务必须重定向输出到日志文件，否则无法调试问题。

```bash
# 标准格式：nohup + disown + 日志重定向
nohup bash -c 'sleep 7200 && cd /path/to/your/workspace && python3 09_soul/tools/opencode_job.py "任务描述" --title "Task Name" --no-wait' > /tmp/delayed_task.log 2>&1 &
disown

# 说明：
# - nohup: 忽略 SIGHUP，shell 关闭后进程不终止
# - > /tmp/xxx.log 2>&1: stdout 和 stderr 都重定向到日志
# - &: 后台运行
# - disown: 从 shell 的 job list 移除，彻底独立
```

### 日志文件命名约定

```bash
# 推荐格式：/tmp/delayed_<task_name>.log
/tmp/dns_check_task.log
/tmp/email_notify.log
/tmp/cleanup_job.log
```

### 查看任务状态和日志

```bash
# 检查任务是否在运行
ps aux | grep "sleep" | grep -v grep

# 实时查看日志
tail -f /tmp/delayed_task.log

# 查看完整日志
cat /tmp/delayed_task.log
```

### 取消延时任务

```bash
# 找到进程 PID
ps aux | grep "sleep 7200" | grep -v grep

# 杀掉进程
kill <PID>
```

## 注意事项
- **必须**使用 `nohup` + `disown` 确保进程持久化
- **必须**重定向输出到日志文件，方便调试
- 长时间任务（>1天）建议用 crontab
- OpenCode 任务适合需要 AI 判断的复杂场景
