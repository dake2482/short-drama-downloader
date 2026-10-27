# short-drama-downloader

> 短剧整季批量下载工具（AI Skill 版）—— 给出链接，还你一整季。

## 📖 简介

**short-drama-downloader** 是一个可独立运行的短剧批量下载 skill。当你拿到 ReelShort 或 ShortLineTV 的剧集链接时，它能自动识别平台、解析整季剧集列表、逐集下载 m3u8 视频流并保存封面，最终在本地生成一个完整的剧集目录（`Episode 01.mp4` ~ `Episode NN.mp4` + `cover.jpg`）。

本项目基于开源项目 [stormwasd/short_drama_downloader](https://github.com/stormwasd/short_drama_downloader)（GUI 版）改造：剥离 GUI 与数据库依赖，重写下载链路以绕过 ReelShort 的 WAF 拦截，专为命令行与 AI Agent 代下载场景设计。

## ✨ 主要功能

| 功能 | 说明 |
|------|------|
| 双平台支持 | ✅ reelshort.com（自动绕过 WAF）／ ✅ shortlinetv.com |
| 整季 / 区间 / 单集下载 | 一次拉全季，或只下第 1-10 集、第 5 集 |
| 自动识别平台 | 根据 URL 自动判断来源，无需手动指定 |
| 剧集信息预览 | `--info` 先看剧名、总集数、封面，不产生下载 |
| 封面自动保存 | 下载剧集同时抓取封面 `cover.jpg` |
| 断点续传（幂等） | 已存在的集自动跳过，中断后重跑即续传 |
| WAF 绕过 | curl_cffi 模拟 Chrome TLS 指纹抓取 ReelShort 页面，提取内嵌 m3u8 直链 |
| /tmp 中转下载 | 规避受控环境下批量文件操作被拦截的问题（详见 SKILL.md 踩坑记录） |

## 🚀 使用方法

### 一键脚本（推荐）

```bash
# 下载整季
./scripts/download.sh "https://www.reelshort.com/episodes/episode-1-xxx-yyy-zzz" ~/Downloads

# 只下第 1~10 集
./scripts/download.sh "https://www.reelshort.com/episodes/episode-1-xxx-yyy-zzz" ~/Downloads 1 10
```

首次运行会自动创建 venv 并安装依赖。逐集下载、每集完成即落盘，中断后重跑自动续传。

### Python 脚本（细粒度控制）

```bash
# 安装依赖
pip install -r requirements.txt

# 预览剧集信息（剧名/集数/封面）
python download_drama.py "<URL>" --info

# 下载全部剧集
python download_drama.py "<URL>"

# 下载指定区间
python download_drama.py "<URL>" --start 1 --end 20

# 指定输出目录与并发数
python download_drama.py "<URL>" --start 1 --end 20 --out ~/Downloads --jobs 2
```

**参数说明**：

| 参数 | 说明 |
|------|------|
| `--info` | 只解析剧集信息，不下载 |
| `--start N` / `--end M` | 下载集数区间（两个平台集号均从 1 开始） |
| `--out <目录>` | 输出目录，默认 `./downloads` |
| `--jobs N` | 并发下载数，默认 2。**ReelShort 建议 ≤2**，过高会触发限流 |

**输出结构**：

```
<输出目录>/
└── <剧名>/
    ├── Episode 01.mp4
    ├── Episode 02.mp4
    ├── ...
    ├── Episode 50.mp4
    └── cover.jpg
```

## 📦 依赖要求

- **Python 3.9+**（3.13 实测通过）
- Python 包：`yt-dlp`、`requests`、`aiohttp`、`curl_cffi`（见 `requirements.txt`）
- macOS / Linux（bash 脚本依赖；Windows 可直接用 `python download_drama.py`）

## 📁 目录结构

```
short-drama-downloader-skill/
├── README.md               # 本文件
├── SKILL.md                # AI Skill 定义（方法论、踩坑记录、故障排查）
├── requirements.txt        # Python 依赖清单
├── download_drama.py       # 核心下载脚本（命令行入口）
├── scripts/
│   └── download.sh         # 一键下载脚本（自动装依赖、逐集下载、断点续传）
└── src/
    ├── __init__.py
    ├── api_clients.py      # 平台 API 客户端（改自原版，双平台解析）
    └── config.py           # 配置（文件名清洗、扩展名、超时等）
```

## 🧯 故障排查

| 现象 | 处理 |
|------|------|
| ReelShort 返回 403 | 正常现象：WAF 拦状态码但放行内容，脚本自动处理；若 body 也变短则等待几分钟后重试 |
| 连续下载失败 | 降低并发（`--jobs 1`）或加大每集间隔 |
| buildId 失效（404） | ReelShort 站点更新了构建 ID，从任意剧集页 HTML 重新提取 `"buildId":"xxx"` 并更新脚本 |
| 部分集失败 | 直接重跑，幂等设计只补失败的集 |

更多技术细节（WAF 绕过原理、沙箱规避、页面结构分析）见 [SKILL.md](SKILL.md)。

## 📄 License

基于原项目 [stormwasd/short_drama_downloader](https://github.com/stormwasd/short_drama_downloader) 改造，仅供个人学习与研究使用。请尊重平台版权，勿将下载内容用于商业用途。
