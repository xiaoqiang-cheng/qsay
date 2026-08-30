# dox

轻量、模型优先的自然语言命令路由器。输入一句话，dox 让 LLM 生成结构化命令计划，展示假设和风险，确认后再执行。

## 当前状态

这是 P0 原型，默认使用 OpenAI-compatible API Provider，可路由任意命令请求（由模型能力和安全校验共同决定）。支持 macOS/Linux 和 PowerShell 目标；Windows `cmd.exe` 不在支持范围内。

## 开发构建

需要 Rust 1.85+：

```bash
cargo test
cargo build --release
```

生成的单文件二进制位于 `target/release/dox`。P0 不需要 Python、Node.js 或本地模型文件；API 模式需要网络。未来的本地 Provider 仍会保持可选。

## 使用

```bash
dox "解压 backup.tar 到 ./backup"
dox --print "extract backup.zip into ./backup"
dox --json "解压 backup.tar 到 ./backup"
```

首次使用前配置 API Provider（API Key 只从环境变量读取）：

```bash
dox config --global llm.provider openai-compatible
dox config --global llm.base-url https://api.openai.com/v1
dox config --global llm.model gpt-4o-mini
dox config --global llm.api-key-env OPENAI_API_KEY
export OPENAI_API_KEY="你的 API Key"
```

其他 OpenAI-compatible 服务只需替换 `llm.base-url`、`llm.model` 和 API Key 环境变量。默认会先展示命令并等待确认。使用 `--print` 只输出命令，使用 `--copy` 复制命令，使用 `--yes` 跳过普通确认（高风险命令仍不能自动执行）。

默认界面为中文，但英文输入可直接使用；英文界面可通过 `--lang en` 开启：

```bash
dox --lang en --print "extract backup.zip into ./backup"
```

用户配置采用 Git 风格的层级配置和 `dox config` 子命令。优先级为：系统级 < 全局 < 当前目录 < 命令行参数。

配置文件路径为：

- 系统级：macOS/Linux `/etc/doxconfig`；Windows `%PROGRAMDATA%\\dox\\config`
- 全局：macOS/Linux `~/.doxconfig`；Windows `%USERPROFILE%\\.doxconfig`
- 当前目录：`./.dox/config`

查看和修改配置：

```bash
dox config --list
dox config --global core.language en
dox config --local llm.model gpt-4o-mini
dox config --unset --global llm.model
```

配置文件是简单 TOML 子集，例如：

```toml
[core]
language = "zh"

[llm]
provider = "openai-compatible"
base-url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api-key-env = "OPENAI_API_KEY"
timeout-seconds = "60"
```

也可以用 `--config PATH` 临时指定单个配置文件：

```bash
dox --config ./dox-test.toml --print "extract backup.zip into ./backup"
```

## 开发与测试

确认工具链已安装：

```bash
rustc --version
cargo --version
```

开发时直接运行，不需要手动编译：

```bash
cargo run -- --help
cargo run -- --print "解压 backup.tar 到 ./backup"
cargo run -- --json "解压 backup.tar 到 ./backup"
```

`cargo run --` 中的 `--` 用于分隔 Cargo 参数和 dox 参数。`--print`、`--json` 只生成计划，不会接触文件。

运行测试：

```bash
cargo fmt --check
cargo test
```

构建优化后的单文件版本：

```bash
cargo build --release
./target/release/dox --print "解压 backup.tar 到 ./backup"
```

如果希望像普通命令一样调用，可以安装到当前用户的 Cargo bin 目录：

```bash
cargo install --path .
dox --print "解压 backup.tar 到 ./backup"
```

### 端到端解压测试

以下测试只在临时目录中创建文件。`--yes` 会真正执行命令，其他模式默认不会执行：

```bash
DOX_TEST_DIR="$(mktemp -d)"
mkdir -p "$DOX_TEST_DIR/source" "$DOX_TEST_DIR/out"
printf 'dox smoke test\n' > "$DOX_TEST_DIR/source/hello.txt"
tar -cf "$DOX_TEST_DIR/archive.tar" -C "$DOX_TEST_DIR/source" hello.txt

cargo run -- --yes "解压 '$DOX_TEST_DIR/archive.tar' 到 '$DOX_TEST_DIR/out'"
cat "$DOX_TEST_DIR/out/hello.txt"
```

Windows PowerShell 的等价测试可以使用：

```powershell
$DoxTestDir = Join-Path $env:TEMP "dox-smoke"
New-Item -ItemType Directory -Force "$DoxTestDir\source", "$DoxTestDir\out" | Out-Null
Set-Content "$DoxTestDir\source\hello.txt" "dox smoke test"
tar -cf "$DoxTestDir\archive.tar" -C "$DoxTestDir\source" hello.txt

cargo run -- --yes "解压 '$DoxTestDir\archive.tar' 到 '$DoxTestDir\out'"
Get-Content "$DoxTestDir\out\hello.txt"
```

`--api` 可以强制使用 API Provider；`--offline` 和 `--local` 会在本版本明确提示本地 Provider 尚未接入。API 返回的计划会经过 JSON、Shell、风险和控制字符校验，默认仍需确认后执行。

## 设计文档

详见 [MVP.md](MVP.md) 和 [本地小模型调研](LOCAL-MODEL-RESEARCH.md)。
