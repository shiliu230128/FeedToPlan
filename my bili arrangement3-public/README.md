# Bili Arrangement 3

> 一句话说出你想要什么，AI 自动抓取、过滤、编排，直接输出可用的内容计划。

---

## 是什么

一个**个性化内容消费提效工具**。

你只需要用自然语言说明需求，比如："我想要这周的瑜伽跟练计划，每天30分钟，膝盖不好不要深蹲"。AI 会理解你的意图，调用本地程序从 B站（可选 YouTube）抓取匹配内容，过滤掉不相关、重复、付费或低质量视频，再根据你的目标、时间和约束完成编排。

**解决的核心问题：** 你知道自己想看什么，但每次都要搜索、筛选、判断质量、再安排顺序。这个工具把这套内容消费前置工作自动化。

**核心卖点：** 限定条件可以动态叠加。主题、目标、时长、身体限制、偏好 UP 主、是否只看最近内容，都可以组合成精准的抓取和编排条件。

**支持的主题：** 不限于瑜伽、健身、冥想、疗愈音乐、学习、编程、财经、知识科普。只要是 B站上能搜索和筛选的内容领域，都可以编排。

---

## 能做什么

- **自然语言驱动**：直接说需求，AI 解析后自动执行，不需要手动调参数
- **优先你关注的博主**：从关注列表识别相关 UP，权重高于全站搜索结果
- **智能过滤**：多维度过滤系统——自动从 notes 中识别时段偏好（晚上练→排除晨间内容）、空间限制（站立/坐姿→强制匹配姿势标签）、强度要求、器械限制、适用人群等语义约束；同时提取"不要X""避免X"等否定短语作为实时过滤词
- **14 天去重**：不会重复推荐你最近用过的视频
- **内容质量保证**：抓取时实时校验每个候选视频的可访问性，自动跳过已删除/失效内容，并从本地缓存中定期清理失效条目
- **记住你的偏好**：近期目标、身体限制、时长偏好会被记录，下次不用反复说明
- **输出形式灵活**：不只是周计划，也可以是每日清单、跟练组合、学习路径、主题片单等
- **可选接入 YouTube**：需要时可以同时抓取 B站和 YouTube 来源

**实现逻辑：**

1. AI 解析你的需求，提取主题、目标、约束、时长和来源范围
2. 本地程序从 B站 API 拉取关注 UP 最新投稿、关键词搜索结果或指定链接
3. 程序按主题词表、播放量、付费状态、时长、来源多样性和历史记录过滤候选内容，并根据 notes 中的语义约束（时段/空间/强度/器械/姿势）进行多维度匹配过滤，同时实时校验每个候选视频的可访问性
4. 生成 `pack.json` 候选池、`prompt.md` 编排提示词和 `draft.md` 本地草稿
5. AI 读取候选池和提示词，输出最终可用的内容安排

---

## 开始使用

> 这是一次性的环境配置。配置完成后，日常使用时只需要向 AI 说你的需求。

### 前提：检查 Python 版本

打开终端（macOS 按 `Cmd+Space`，搜索"终端"），输入：

```bash
python3 --version
```

看到 `Python 3.9.x` 或更高版本就可以。如果提示找不到命令，或版本低于 3.9，需要先访问 [python.org/downloads](https://www.python.org/downloads/) 安装新版 Python。

---

### 第一步：安装项目

先获取项目文件夹路径。

如果项目在 Finder 里：选中文件夹，按住 `Option`，右键点击文件夹，选择"复制 xxx 的路径名"。复制到的内容通常类似：

```text
/Users/你的用户名/Desktop/my bili arrangement3-public
```

然后在终端执行下面两行。第一行里的路径直接粘贴你刚才复制到的项目路径：

```bash
cd "这里粘贴你复制到的项目路径"
python3 -m pip install -e .
```

看到 `Successfully installed bili-arrangement3` 或类似提示，就说明安装完成。

安装后可以检查入口是否可用：

```bash
bili-arrangement3 --help
```

---

### 第二步：配置 B站 Cookie

Cookie 是让程序以你的登录身份访问 B站的凭证，用来读取关注列表、UP 投稿和部分搜索结果。通常只需要配置一次。

**第 1 步：获取 Cookie**

1. 用 Chrome（或 Edge）打开 [bilibili.com](https://www.bilibili.com)，确保已经登录
2. 按 `Cmd+Option+I`（Mac）或 `F12`（Windows）打开开发者工具；也可以右键页面任意位置，选择"检查"
3. 点击顶部 **Network（网络）** 标签页；如果标签页是空的，按 `Cmd+R`（Mac）或 `F5`（Windows）刷新页面就会看到请求列表出现
4. 在左侧请求列表里，点击任意一条域名为 `www.bilibili.com` 的请求
5. 在右侧面板找到 **Request Headers（请求标头）**；如果被折叠了，点击标题展开
6. 找到 `cookie:` 这一行，在这一行的值区域**三击**（连点三下）即可全选，然后 `Cmd+C` 复制

复制到的内容很长，里面有很多 `=` 和 `;`，这是正常的。

> 如果 Request Headers 里没看到 `cookie:` 行，换一条 `www.bilibili.com` 的请求再试一次，部分请求可能不携带完整 Cookie。

**第 2 步：写入配置**

为了方便操作，**先在终端里把下面这行命令粘贴好，但先不要按回车**：

```bash
pbpaste | bili-arrangement3 auth set-cookie --stdin
```

然后回到浏览器，按上述第 1 步复制了 Cookie 之后，再切回终端，直接按回车执行。

> 小技巧：也可以在第 2 步先不粘 `pbpaste` 那一行，而是在终端输入 `bili-arrangement3 auth set-cookie --stdin` 然后按回车（这时终端会等待输入），再粘贴 Cookie 内容，按 `Ctrl+D`（Mac）或 `Ctrl+Z` 后回车（Windows）结束输入。两种方式都可以。

**第 3 步：验证**

```bash
bili-arrangement3 auth status
```

看到 `cookie_configured=true` 就配置成功。

> Cookie 会写入项目内 `.secrets/bilibili_cookie.txt`，也会同步写入 `~/.config/bili-secrets/bilibili_cookie.txt`，方便以后项目改名或复制后继续使用。不要把 `.secrets/` 分享给别人。

---

### 第三步：配置 AI 使用方式

这个项目有两种 AI 使用方式。两者区别在于：AI 能不能直接操作你本地的项目文件和终端。

#### 用法一：AI 平台全自动编排（推荐）

适合 Comate、Claude Code、Cursor、CodeBuddy 等**能在本地运行命令**的 AI 平台。

这种模式下，AI 可以自己运行 `bili-arrangement3` 命令、读取候选池文件、直接输出最终安排。你只需要让 AI 知道"这个工具怎么用"——通过安装本项目自带的 skill。

**skill 文件在哪**

刚才安装项目时 `cd` 进去的那个文件夹，里面有一个 `skill/bilibili-arrangement3/` 子文件夹，包含：

- `SKILL.md` — 告诉 AI 怎么使用这个工具的主提示词
- `references/` — 编排规则、用户引导模板

**如何安装 skill（按你的平台选一种）**

- **Comate（桌面版 / CLI）：** 在终端里把下面路径中的文件夹位置换成你实际的，然后执行：

  ```bash
  comate skill add "你的项目文件夹路径/skill/bilibili-arrangement3"
  ```

  如果不确定项目路径，在 Finder 里选中项目文件夹，`Option + 右键` → "复制路径名"，然后把结果拼上 `/skill/bilibili-arrangement3`。

- **Claude Code / CodeBuddy / Cursor / 其他支持 skill 或 MCP 的平台：**
  找到项目里的 `skill/bilibili-arrangement3/` 整个文件夹，在平台的 skill / MCP 管理界面里导入或上传。每种平台的操作入口不一样，具体参考该平台的 skill 添加文档。

- **不支持安装 skill 的平台：**
  如果平台没有 skill 导入功能，可以打开 `skill/bilibili-arrangement3/SKILL.md`，把全文复制粘贴到平台的"系统提示词"或"自定义指令"设置里。效果接近，只是每次对话时需要先告诉 AI 你的项目路径。

skill 配置好之后，在 AI 对话里直接说需求，例如：

> 帮我生成这周的瑜伽跟练计划，每天早上30分钟，目标是缓解久坐肩颈问题，膝盖不太好，避免深蹲和跳跃。

AI 会自动完成：理解需求 → 调用本地抓取 → 过滤内容 → 生成候选池 → 读取 prompt → 最终编排。

#### 用法二：本地获取内容信息，再粘贴给任意 AI 编排

适合 ChatGPT 网页版、Claude 网页版、豆包等**不能直接访问你本地终端和文件**的 AI。

这种模式下，你先在本地终端运行抓取命令：

```bash
bili-arrangement3 plan --topic "瑜伽" --scope mixed --days 7 --duration-max 30 --notes "膝盖不好，避免深蹲和跳跃"
```

运行完成后，会生成 `outputs/runs/...` 目录，其中最重要的是：

- `prompt.md`：把这个文件内容粘贴给任意 AI，AI 会根据候选池信息输出最终编排
- `pack.json`：候选视频池，包含标题、链接、UP 主、发布时间、播放量、时长和过滤后的结构化信息
- `draft.md`：不用 AI 的本地草稿，算法直接分配，可以临时参考

如果你只是想验证程序能不能跑，也可以先执行：

```bash
bili-arrangement3 plan --offline
```

---

### 可选：配置 AI API Key

OpenAI API Key 不是必需项。它的作用不是替代平台里的 AI 对话，而是让本地程序在抓取和生成候选池时更智能。

配置后，本地程序可以：

- 为内置词表以外的新主题自动生成过滤词表
- 生成更贴合主题的 7 个编排槽位，例如"入门建立"、"专项强化"、"恢复收尾"
- 提高非运动类主题的初筛质量，减少无关视频进入候选池
- 在没有平台级 AI 接管时，让本地 `prompt.md` 和 `draft.md` 更贴近你的主题

如果不配置，也可以正常使用。项目已经内置了瑜伽、冥想、居家健身、疗愈音乐、普拉提、跑步、减脂、睡眠助眠、拉伸恢复、烹饪、学习、编程、财经、知识科普等常见主题词表。

如果需要配置 OpenAI API Key：

1. 访问 [platform.openai.com](https://platform.openai.com)，登录后进入 API keys 页面
2. 点击 "Create new secret key"，复制生成的 key（通常以 `sk-` 开头）
3. 在项目根目录执行：

```bash
mkdir -p .secrets
printf "%s" "sk-你的key粘贴在这里" > .secrets/openai_api_key.txt
```

也可以不写文件，临时通过环境变量传入：

```bash
OPENAI_API_KEY="sk-你的key粘贴在这里" bili-arrangement3 plan --topic "你想编排的主题"
```

---

## 可选：接入 YouTube

如果你希望同时从 YouTube 搜索，可以配置 YouTube Data API v3 key。

1. 访问 [Google Cloud Console](https://console.cloud.google.com)，创建项目
2. 启用 YouTube Data API v3，创建 API Key
3. 在项目根目录执行：

```bash
mkdir -p .secrets
printf "%s" "AIzaSy你的key" > .secrets/youtube_api_key.txt
```

使用时告诉 AI "同时从 YouTube 搜索"，或在终端命令里加：

```bash
bili-arrangement3 plan --platform all --topic "疗愈音乐"
```

---

## 常见问题

**Q: 项目名称和命令是什么？**
项目统一使用 Arrangement 3。对外命令是 `bili-arrangement3`，skill 名是 `bilibili-arrangement3`，Python 包名是 `bili_arrangement3`。

**Q: 提示 `cookie_configured=false` 或抓取时报错？**
Cookie 可能失效了。重新按"第二步"获取并写入一次 Cookie。

**Q: 抓出来的内容很少或候选池为空？**
可能是 B站频率限制、关注列表里相关 UP 太少，或主题太窄。可以等 30 秒再重试，也可以使用全站搜索：`bili-arrangement3 plan --scope topic --topic "你的主题"`。

**Q: 我想换个主题，能用吗？**
可以。直接说需求即可。内置主题会走内置词表；内置词表以外的主题，如果配置了 OpenAI API Key，会自动生成主题词表；没配置时会使用通用词表，效果略弱但仍可用。

**Q: 我的关注列表里没有相关博主怎么办？**
让 AI 使用全站搜索模式，或者直接提供 UP 主页链接、视频链接。程序支持把这些临时来源加入本次抓取。

**Q: Cookie 和 API Key 安全吗？**
Cookie、OpenAI Key、YouTube Key 都保存在你电脑本地，不会上传到项目仓库。分享项目给别人前，不要包含 `.secrets/` 目录。

**Q: 我指定了"只能站立/坐姿"，推荐的视频真的都符合吗？**
系统会强制匹配——notes 中检测到空间约束后，标题/简介/标签不含"站立/坐姿/椅子"等关键词的视频会被直接拒绝（不会出现在候选池中）。如果候选不足，编排结论中会明确告知。

**Q: 为什么有些推荐视频点开发现失效了？**
B站部分搬运类视频生命周期极短（可能几小时内被下架）。系统在抓取时会实时校验每个候选的可访问性，失效视频自动跳过并清理出缓存。但抓取和用户点击之间存在极小的时间窗口，无法完全避免。

**Q: notes 里可以写多详细的约束？**
可以写自然语言。系统支持识别：时段偏好（晚上/晨间）、空间限制（站立/坐姿/椅子）、强度要求（低强度/温和）、器械限制（无器械/徒手）、否定约束（不要深蹲/避免跳跃）、适用人群（新手/经期/膝盖不好）。多个约束用逗号或句号分隔即可，越具体过滤越精准。

---

## 开发与贡献

欢迎提 Issue 和 PR。

### 本地开发

```bash
git clone <repo-url>
cd bili-arrangement3
python3 -m pip install -e .        # 可编辑安装
python3 -m unittest discover -s tests  # 运行测试
```

### 添加新主题词表

编辑 `src/bili_arrangement3/topic_profile.py`，参照已有的 `_BUILTIN_YOGA` 结构添加新的 `_BUILTIN_*` 字典，并注册到 `_BUILTIN_PROFILES` 和 `_BUILTIN_ALIASES` 中。同时可以添加对应的 `filter_dimensions` 维度定义。

### Skill 独立安装

项目自带的 AI skill 位于 `skill/bilibili-arrangement3/`。如果只想安装 skill 而不安装完整项目，可以下载 `skill/bilibili-arrangement3.zip`，在你的 AI 平台中导入该 zip 文件。
