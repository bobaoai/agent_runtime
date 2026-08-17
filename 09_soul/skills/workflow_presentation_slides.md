# AI-Generated Presentation Slides Workflow

## Local Status

已弃用（保留脚印）。

原因：
- 这是独立 slides repo 的工作流，不是当前 repo 的原生能力
- 关键脚本 `generate_slides.py` 未导入到本地环境
- 文档仍然有价值，可作为未来恢复或迁移时的操作模板

## 元数据

- **类型**: Workflow
- **适用场景**: 制作高质量演示文稿，适用于企业内训、技术分享、keynote 等
- **Repo**: [github.com/grapeot/nbp_slides](https://github.com/grapeot/nbp_slides)
- **创建日期**: 2026-02-25
- **来源**: AI 演讲实践总结

---

## 核心理念

使用 AI 图像生成（Gemini）将演讲内容"渲染"成完整的 slide deck，而非手动组装。每张幻灯片是一张完整的高分辨率图像，文字和视觉元素作为一个整体被生成。

**关键区别**：

- 旧方式：用 PowerPoint/Keynote 拖拽元素，逐个对齐
- 新方式：写 Markdown prompt，用 Gemini 渲染整张图，用 Reveal.js 播放

---

## 第一步：Clone Repo

```bash
git clone <your-slides-repo>
cd <slides-repo-dir>
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

创建 `.env` 文件：
```
GEMINI_API_KEY=your_key_here
```

---

## 第二步：确定 Visual Style

编辑 `visual_guideline.md`。这是所有生成的"视觉锚点"。

### 参考风格：**"Clean Ink"**

```
- 背景：冷浅灰 (#F0F4F8) + 极细网格
- 插图风格：深海军蓝线条，flat color fills，工程图纸的精准感
- 排版：无衬线字体（Inter/SF Pro），每张必有粗体 header
- 禁止：写实照片、光泽感 3D、通用 clipart
- 吉祥物：<your-mascot>（仅用于标题页和收尾页）
```

### 关键平衡原则（非常重要）

**Slides 的终极设计目标是 Dual-Use：既是 Presentation 工具，也是 Handout。**

这是最核心的 trade-off。一端是 Steve Jobs 风格（纯视觉，离开演讲者就看不懂）；另一端是老师上课风格（纯文字，PPT 死亡综合征）。我们要在中间找到 sweet spot：

- **作为 Handout**：如果把 slides 发给没参加演讲的人，他们仅靠阅读 slides 就能理解核心论点，不需要额外解释
- **作为 Presentation**：迟到或走神的听众能通过当前 slide 快速 catch up 到讲者的位置

这个双重要求决定了 slide 上的文字不能只是「标签」或「关键词提示」（那是 Steve Jobs 风格），而必须是**实际的论点和内容**——但又不能是整段整段的文章。

**具体执行标准：**

- **目标比例**：~40% 插图 / ~60% 可读文字
- **布局模型**：左右分栏——左侧放图示/流程图/概念图，右侧放 2-4 行关键文字
- **文字选择**：slide 上的文字应该是这个 slide 的核心论点本身，而不是对论点的引用或暗示。读者看到文字就知道你在说什么
- **视觉选择**：插图应该是概念图/对比图/流程图（帮助理解论点），而不是装饰性配图（只填充空间）
- 文字要能在演讲台后排被清晰阅读
- **自检方法**：写完一页 slide 后，遮住 speaker notes，问自己——一个没听过这个 talk 的聪明人，仅看这页 slide，能理解这个 slide 想说什么吗？如果不能，文字不够；如果能但觉得无聊，视觉不够

### 颜色语义（对比类演讲特别有用）

- **Orange (#C75B39)**：旧范式 / 问题侧（如 ChatGPT、Before）
- **Teal (#0A6A74)**：新范式 / 解决方案侧（如 Cursor、After）
- **Navy (#1C2526)**：正文墨色

---

## 第三步：写 outline_visual.md

这是 slide deck 的"源代码"。每个 slide 的格式：

```markdown
#### Slide N: 标题名称
*   **Layout**: 布局描述（如 Left diagram + right text panel）
*   **Scene**:
    *   **Prompt**: [详细的视觉描述，包含：
        - 粗体 header 文字
        - 插图区的具体内容和风格
        - 文字区的实际可读文字内容（逐字写清楚）
        - 颜色、线条、排版指令]
*   **Asset**: imgs/某个logo.png  ← 可选，有 logo 或截图时使用
```

### Prompt 写作要点

1. **文字内容要逐字写入 prompt**，不能只说"加一些说明文字"
2. 插图描述要具体（"大圆圈，左下有缺口，人形剪影站在缺口中"）
3. 文字区内容要完整（不是 placeholder，是实际内容）
4. 两侧内容要在 prompt 里明确标注 `LEFT PANEL` / `RIGHT PANEL`

### 关于 "not" 句式

**避免在 slide 文字中使用否定句型**（如 "you're not a user", "it doesn't know"）。  
改用正向陈述表达同等含义，例如：

| 避免 | 改用 |
|------|------|
| "you're not a user of the tool" | "you end up serving as a component of the tool" |
| "it doesn't know your config" | "it goes in blind: config unknown" |
| "this isn't just faster" | "this is a categorical shift" |
| "not just coding" | "brainstorming, drafting, planning, everything" |

---

## 第四步：准备 Assets（可选）

如果 slide 中涉及品牌 logo、截图、QR 码：

1. 将文件放入 `imgs/` 目录
2. 在 `outline_visual.md` 对应 slide 中加一行：`*   **Asset**: imgs/filename.png`
3. 生成时 prompt 会自动注入该图片

**常见 asset 来源**：
- 公司 logo：从官网或 uxwing.com 下载 PNG
- 截图：直接截取并保存
- QR 码：使用 Python `qrcode` 库或在线工具生成

⚠️ AI 无法可靠地生成品牌 logo（会产生幻觉）。需要 logo 时，务必提供真实文件作为 asset。

---

## 第五步：生成 1K 版本（快速迭代）

```bash
# 生成所有 slides（8 进程并行，速度快）
python tools/generate_slides.py

# 只生成特定 slides（迭代修改时）
python tools/generate_slides.py --slides 3 5 8
```

生成的文件在 `generated_slides/slide_XX_0.jpg`（1K 分辨率）。

**注意**：generate_slides.py 里的 `ThreadPoolExecutor(max_workers=8)` 要设为 8，默认可能是 4。

---

## 第六步：预览

```bash
python start-server.py  # 或直接打开 index.html
```

`index.html` 使用 Reveal.js 显示图片，按 `S` 键打开 speaker notes 模式。

---

## 第七步：Enlarge 到 4K（正式发布前）

**关键：先在单张图上实验，确认能 enlarge 到 4K 或以上，再全量处理。**

```bash
# Step 1：先测试单张
python tools/generate_slides.py --enlarge --slides 1

# 验证：查看 generated_slides/slide_01_0_4k.jpg 的尺寸
file generated_slides/slide_01_0_4k.jpg
# 应显示类似 "3840 x 2160" 或更大

# Step 2：确认可以 enlarge 后，全量并行处理
python tools/generate_slides.py --enlarge
```

⚠️ Enlarge 操作会重新调用 Gemini API，成本较高。先用单张验证是必须的步骤。

---

## 第八步：更新 index.html

`index.html` 里每个 section 的 `data-background` 路径：

- 1K 版本：`generated_slides/slide_XX_0.jpg`
- 4K 版本：`generated_slides/slide_XX_0_4k.jpg`

Speaker notes 写在每个 section 的 `<aside class="notes">` 里。

---

## Speaker Notes 写作原则

- 面向 native speaker 的英文，口语化，可以直接照念
- 每张 ~120-150 词
- **避免否定句型**（同 slide 文字原则）
- 第一人称，用"I"，有温度，有观点
- 具体细节优先于抽象总结

---

## 常见问题排查

| 问题 | 原因 | 解决 |
|------|------|------|
| AI 画出来的 logo 是乱的 | AI 幻觉，logo 不是真实像素 | 提供 `imgs/` 中的真实 logo 文件作为 asset |
| 风格不一致 | 各 slide prompt 之间差异太大 | 在 `visual_guideline.md` 里加强"container"描述；或重新生成 |
| 文字乱码 / 看不清 | 生成的字体太小或太花哨 | 在 prompt 中明确："all text must be fully legible printed sans-serif" |
| AI 把鼠标光标当 Cursor | 混淆了 Cursor 公司和 cursor 光标 | 提供 Cursor 公司 logo 作为 asset，prompt 明确说 "Cursor company logo" |

---

## 项目文件结构

```
slides/
├── outline_visual.md      # 源代码（每次改这里）
├── visual_guideline.md    # 视觉语言定义
├── speak_notes.md         # 演讲稿（照着念的英文）
├── index.html             # Reveal.js 播放器 + speaker notes
├── imgs/                  # Assets（logo、截图等）
├── generated_slides/      # 渲染输出
│   ├── slide_01_0.jpg     # 1K 版
│   └── slide_01_0_4k.jpg  # 4K 版
├── tools/
│   ├── generate_slides.py      # 主生成器（max_workers=8）
│   ├── gemini_generate_image.py # Gemini API 封装
│   └── gemini_enlarge_image.py  # 4K 放大器
└── .env                   # GEMINI_API_KEY=...
```
