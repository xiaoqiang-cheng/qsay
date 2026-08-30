use std::collections::BTreeMap;
use std::env;
use std::io::{self, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::Duration;

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Platform {
    Macos,
    Linux,
    Windows,
    Other,
}

impl Platform {
    fn current() -> Self {
        if cfg!(target_os = "macos") {
            Self::Macos
        } else if cfg!(target_os = "linux") {
            Self::Linux
        } else if cfg!(target_os = "windows") {
            Self::Windows
        } else {
            Self::Other
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Macos => "macos",
            Self::Linux => "linux",
            Self::Windows => "windows",
            Self::Other => "other",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Language {
    Chinese,
    English,
}

impl Language {
    fn parse(value: &str) -> Option<Self> {
        match value.trim().to_lowercase().as_str() {
            "zh" | "zh-cn" | "中文" | "chinese" => Some(Self::Chinese),
            "en" | "en-us" | "english" | "英文" => Some(Self::English),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
struct Config {
    language: Language,
    provider: String,
    base_url: String,
    model: String,
    api_key_env: String,
    timeout_seconds: u64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            language: Language::Chinese,
            provider: "openai-compatible".to_string(),
            base_url: "https://api.openai.com/v1".to_string(),
            model: String::new(),
            api_key_env: "OPENAI_API_KEY".to_string(),
            timeout_seconds: 60,
        }
    }
}

#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Risk {
    ReadOnly,
    Write,
    High,
}

impl Risk {
    fn as_str(self) -> &'static str {
        match self {
            Self::ReadOnly => "read_only",
            Self::Write => "write",
            Self::High => "high",
        }
    }

    fn label(self, language: Language) -> &'static str {
        match language {
            Language::Chinese => match self {
                Self::ReadOnly => "只读",
                Self::Write => "会写入文件",
                Self::High => "高风险操作",
            },
            Language::English => match self {
                Self::ReadOnly => "read-only",
                Self::Write => "writes files",
                Self::High => "high-risk operation",
            },
        }
    }
}

#[derive(Debug, Clone)]
struct CommandSpec {
    program: String,
    args: Vec<String>,
}

#[derive(Debug, Clone)]
struct IntentPlan {
    intent: String,
    source: String,
    destination: String,
    tool: String,
    platform: Platform,
    risk: Risk,
    assumptions: Vec<String>,
    steps: Vec<CommandSpec>,
    display_command: String,
}

impl IntentPlan {
    fn to_json(&self) -> String {
        let assumptions = self
            .assumptions
            .iter()
            .map(|item| format!("\"{}\"", json_escape(item)))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"intent\":\"{}\",\"args\":{{\"source\":\"{}\",\"destination\":\"{}\"}},\"platform\":\"{}\",\"tools\":[\"{}\"],\"risk\":\"{}\",\"assumptions\":[{}],\"command\":\"{}\"}}",
            json_escape(&self.intent),
            json_escape(&self.source),
            json_escape(&self.destination),
            self.platform.as_str(),
            json_escape(&self.tool),
            self.risk.as_str(),
            assumptions,
            json_escape(&self.display_command),
        )
    }
}

#[derive(Debug)]
enum PlanOutcome {
    Plan(IntentPlan),
    Clarification(String),
    Unsupported(String),
}

#[derive(Debug, Default)]
struct Options {
    help: bool,
    print_only: bool,
    copy: bool,
    json: bool,
    yes: bool,
    explain: bool,
    api: bool,
    local: bool,
    offline: bool,
    language: Option<Language>,
    config_path: Option<PathBuf>,
    request: Vec<String>,
}

fn main() -> ExitCode {
    let raw_args = env::args().skip(1).collect::<Vec<_>>();
    if raw_args.first().map(String::as_str) == Some("config") {
        return run_config_command(&raw_args[1..]);
    }

    let options = match parse_options(raw_args) {
        Ok(options) => options,
        Err(message) => {
            eprintln!("错误：{message}");
            print_usage(Language::Chinese);
            return ExitCode::from(2);
        }
    };

    let config = load_config(options.config_path.as_deref());
    let language = options.language.unwrap_or(config.language);

    if options.help {
        print_usage(language);
        return ExitCode::SUCCESS;
    }

    if options.request.is_empty() {
        print_usage(language);
        return ExitCode::from(2);
    }

    let request = options.request.join(" ");
    let platform = Platform::current();
    if options.offline || options.local || (!options.api && config.provider == "local") {
        match language {
            Language::Chinese => eprintln!(
                "当前版本尚未接入本地模型 Provider；请移除 `--offline`/`--local`，或配置 API Provider。\n"
            ),
            Language::English => eprintln!(
                "The local model provider is not available yet. Remove `--offline`/`--local`, or configure an API provider.\n"
            ),
        }
        return ExitCode::from(2);
    }

    let provider = if options.api {
        "openai-compatible"
    } else {
        config.provider.as_str()
    };
    let outcome = match provider {
        "openai-compatible" | "openai" | "api" => {
            match plan_with_api(&request, platform, language, &config) {
                Ok(outcome) => outcome,
                Err(message) => {
                    match language {
                        Language::Chinese => eprintln!("API 请求失败：{message}"),
                        Language::English => eprintln!("API request failed: {message}"),
                    }
                    return ExitCode::from(2);
                }
            }
        }
        provider => PlanOutcome::Unsupported(match language {
            Language::Chinese => format!("未知 Provider `{provider}`"),
            Language::English => format!("Unknown provider `{provider}`"),
        }),
    };

    match outcome {
        PlanOutcome::Clarification(message) => {
            match language {
                Language::Chinese => println!("需要补充信息：{message}"),
                Language::English => println!("More information is needed: {message}"),
            }
            ExitCode::from(1)
        }
        PlanOutcome::Unsupported(message) => {
            match language {
                Language::Chinese => {
                    println!("暂不支持：{message}");
                    println!("模型未能生成可验证的命令计划；请改写请求或补充参数。");
                }
                Language::English => {
                    println!("Not supported yet: {message}");
                    println!("The model could not produce a verifiable command plan; rephrase the request or add details.");
                }
            }
            ExitCode::from(1)
        }
        PlanOutcome::Plan(plan) => run_plan(plan, &options, language),
    }
}

fn parse_options(arguments: Vec<String>) -> Result<Options, String> {
    let mut options = Options::default();
    let mut args = arguments.into_iter().peekable();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--help" | "-h" => {
                options.help = true;
            }
            "--print" => options.print_only = true,
            "--copy" => options.copy = true,
            "--json" => options.json = true,
            "--yes" => options.yes = true,
            "--explain" => options.explain = true,
            "--api" => options.api = true,
            "--local" => options.local = true,
            "--offline" => options.offline = true,
            "--lang" => {
                let value = args
                    .next()
                    .ok_or_else(|| "`--lang` 需要 `zh` 或 `en`".to_string())?;
                options.language = Language::parse(&value);
                if options.language.is_none() {
                    return Err(format!("不支持的语言 `{value}`，可选值为 `zh` 或 `en`"));
                }
            }
            "--config" => {
                let value = args
                    .next()
                    .ok_or_else(|| "`--config` 需要一个配置文件路径".to_string())?;
                options.config_path = Some(PathBuf::from(value));
            }
            value if value.starts_with('-') => {
                return Err(format!("未知选项 `{value}`"));
            }
            value => options.request.push(value.to_string()),
        }
    }
    if options.copy && options.print_only {
        return Err("`--copy` 与 `--print` 不能同时使用".to_string());
    }
    if options.local && options.offline {
        // Both are compatible; keep the flags accepted so the eventual Provider
        // implementation can use them without changing the CLI surface.
    }
    Ok(options)
}

fn plan_with_api(
    request: &str,
    platform: Platform,
    language: Language,
    config: &Config,
) -> Result<PlanOutcome, String> {
    if config.model.trim().is_empty() {
        return Err(match language {
            Language::Chinese => {
                "尚未配置模型。请先运行 `dox config --global llm.model <模型名>`".to_string()
            }
            Language::English => {
                "No model is configured. Run `dox config --global llm.model <model>` first."
                    .to_string()
            }
        });
    }

    let api_key = if config.api_key_env.trim().is_empty() {
        None
    } else {
        Some(
            env::var(&config.api_key_env).map_err(|_| match language {
                Language::Chinese => format!(
                    "环境变量 `{}` 未设置；请设置它，或将 `llm.api-key-env` 配置为空（适用于无需密钥的本地 endpoint）",
                    config.api_key_env
                ),
                Language::English => format!(
                    "Environment variable `{}` is not set. Set it, or make `llm.api-key-env` empty for a local endpoint.",
                    config.api_key_env
                ),
            })?,
        )
    };

    let shell = current_shell(platform);
    let language_name = match language {
        Language::Chinese => "Chinese",
        Language::English => "English",
    };
    let system_prompt = format!(
        "You are dox, a natural-language command router. Return exactly one JSON object and no markdown. Do not provide chain-of-thought or long explanations. Understand the user's request and propose one command for the current environment. If the request is ambiguous or unsafe to complete reliably, set clarification to a concise question and command to an empty string. JSON schema: {{\"intent\":string,\"command\":string,\"shell\":string,\"risk\":\"read_only\"|\"write\"|\"high\",\"assumptions\":[string],\"tools\":[string],\"clarification\":string|null}}. The command must target platform={platform}, shell={shell}, user interface language={language_name}. Never include secrets or pretend to have run the command.",
        platform = platform.as_str(),
        shell = shell,
    );
    let payload = json!({
        "model": config.model,
        "temperature": 0,
        "stream": false,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request},
        ]
    });

    let endpoint = chat_endpoint(&config.base_url);
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(config.timeout_seconds.max(1)))
        .build();
    let mut request_builder = agent
        .post(&endpoint)
        .set("Content-Type", "application/json")
        .set("Accept", "application/json");
    if let Some(api_key) = api_key {
        request_builder = request_builder.set("Authorization", &format!("Bearer {api_key}"));
    }

    let response = request_builder
        .send_string(&payload.to_string())
        .map_err(|error| format_ureq_error(error))?;
    let response_text = response
        .into_string()
        .map_err(|error| format!("无法读取 API 响应：{error}"))?;
    let response_json: Value = serde_json::from_str(&response_text)
        .map_err(|error| format!("API 返回的不是有效 JSON：{error}"))?;
    let content = response_json
        .pointer("/choices/0/message/content")
        .and_then(Value::as_str)
        .ok_or_else(|| "API 响应中缺少 choices[0].message.content".to_string())?;

    parse_model_plan(content, platform, language)
}

fn chat_endpoint(base_url: &str) -> String {
    let base_url = base_url.trim_end_matches('/');
    if base_url.ends_with("/chat/completions") {
        base_url.to_string()
    } else {
        format!("{base_url}/chat/completions")
    }
}

fn format_ureq_error(error: ureq::Error) -> String {
    match error {
        ureq::Error::Status(code, response) => {
            let body = response.into_string().unwrap_or_default();
            if body.is_empty() {
                format!("HTTP {code}")
            } else {
                format!("HTTP {code}: {body}")
            }
        }
        ureq::Error::Transport(error) => error.to_string(),
    }
}

fn parse_model_plan(
    content: &str,
    platform: Platform,
    language: Language,
) -> Result<PlanOutcome, String> {
    let json_text =
        extract_json_object(content).ok_or_else(|| "模型响应中没有找到 JSON 计划".to_string())?;
    let value: Value =
        serde_json::from_str(json_text).map_err(|error| format!("模型计划 JSON 无效：{error}"))?;

    let clarification = value
        .get("clarification")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty());
    if let Some(clarification) = clarification {
        return Ok(PlanOutcome::Clarification(clarification.to_string()));
    }

    let command = value
        .get("command")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| match language {
            Language::Chinese => "模型没有返回可执行命令，也没有提出澄清问题".to_string(),
            Language::English => {
                "The model returned neither a command nor a clarification question.".to_string()
            }
        })?;
    if command.contains('\0') || command.contains('\n') || command.contains('\r') {
        return Err("模型命令包含不允许的控制字符".to_string());
    }

    let model_shell = value
        .get("shell")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| current_shell(platform));
    if !shell_supported(platform, &model_shell) {
        return Err(format!(
            "模型返回了不匹配的平台 Shell `{model_shell}`（当前平台为 {}）",
            platform.as_str()
        ));
    }

    let declared_risk = value
        .get("risk")
        .and_then(Value::as_str)
        .and_then(parse_risk)
        .unwrap_or(Risk::High);
    let risk = max_risk(declared_risk, assess_command_risk(command));
    let assumptions = value
        .get("assumptions")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| {
            vec![match language {
                Language::Chinese => "命令由模型生成，执行前请检查目标和参数".to_string(),
                Language::English => "The command was generated by a model; review its target and arguments before execution.".to_string(),
            }]
        });
    let tool = value
        .get("tools")
        .and_then(Value::as_array)
        .and_then(|items| items.iter().find_map(Value::as_str))
        .unwrap_or("shell")
        .to_string();

    Ok(PlanOutcome::Plan(IntentPlan {
        intent: value
            .get("intent")
            .and_then(Value::as_str)
            .unwrap_or("model.command")
            .to_string(),
        source: String::new(),
        destination: String::new(),
        tool,
        platform,
        risk,
        assumptions,
        steps: build_model_steps(platform, &model_shell, command),
        display_command: command.to_string(),
    }))
}

fn extract_json_object(content: &str) -> Option<&str> {
    let content = content.trim();
    let content = content
        .strip_prefix("```json")
        .or_else(|| content.strip_prefix("```JSON"))
        .or_else(|| content.strip_prefix("```"))
        .unwrap_or(content)
        .trim();
    let start = content.find('{')?;
    let end = content.rfind('}')?;
    (start < end).then(|| &content[start..=end])
}

fn parse_risk(value: &str) -> Option<Risk> {
    match value.trim().to_lowercase().as_str() {
        "read_only" | "readonly" | "read-only" => Some(Risk::ReadOnly),
        "write" | "modify" => Some(Risk::Write),
        "high" | "dangerous" => Some(Risk::High),
        _ => None,
    }
}

fn max_risk(left: Risk, right: Risk) -> Risk {
    let rank = |risk| match risk {
        Risk::ReadOnly => 0,
        Risk::Write => 1,
        Risk::High => 2,
    };
    if rank(left) >= rank(right) {
        left
    } else {
        right
    }
}

fn assess_command_risk(command: &str) -> Risk {
    let lower = command.to_lowercase();
    let high_markers = [
        "rm -rf",
        "rm -r ",
        "remove-item -recurse",
        "format ",
        "format-volume",
        "mkfs",
        "dd if=",
        "diskpart",
        "git reset --hard",
        "sudo ",
        "shutdown",
        "reboot",
        "curl ",
        "wget ",
    ];
    if high_markers.iter().any(|marker| lower.contains(marker)) {
        return Risk::High;
    }
    if lower.contains(" > ")
        || lower.contains(" >> ")
        || lower.contains("mkdir")
        || lower.contains("touch ")
        || lower.contains("cp ")
        || lower.contains("mv ")
        || lower.contains("new-item")
        || lower.contains("set-content")
    {
        Risk::Write
    } else {
        Risk::ReadOnly
    }
}

fn current_shell(platform: Platform) -> String {
    if platform == Platform::Windows {
        "powershell".to_string()
    } else {
        env::var("SHELL").unwrap_or_else(|_| "sh".to_string())
    }
}

fn shell_supported(platform: Platform, shell: &str) -> bool {
    let shell = shell.to_lowercase();
    match platform {
        Platform::Windows => shell.contains("powershell") || shell == "pwsh",
        Platform::Macos | Platform::Linux => {
            shell.ends_with("/sh")
                || shell.ends_with("/bash")
                || shell.ends_with("/zsh")
                || shell.ends_with("/fish")
                || shell == "sh"
                || shell == "bash"
                || shell == "zsh"
                || shell == "fish"
        }
        Platform::Other => false,
    }
}

fn build_model_steps(platform: Platform, shell: &str, command: &str) -> Vec<CommandSpec> {
    if platform == Platform::Windows {
        vec![CommandSpec {
            program: "powershell.exe".to_string(),
            args: vec![
                "-NoProfile".to_string(),
                "-NonInteractive".to_string(),
                "-Command".to_string(),
                command.to_string(),
            ],
        }]
    } else {
        vec![CommandSpec {
            program: shell.to_string(),
            args: vec!["-lc".to_string(), command.to_string()],
        }]
    }
}

fn run_plan(plan: IntentPlan, options: &Options, language: Language) -> ExitCode {
    if options.json {
        println!("{}", plan.to_json());
        return ExitCode::SUCCESS;
    }

    if options.print_only {
        println!("{}", plan.display_command);
        return ExitCode::SUCCESS;
    }

    let intent_label = if plan.intent.is_empty() {
        match language {
            Language::Chinese => "未命名意图",
            Language::English => "unnamed intent",
        }
    } else {
        plan.intent.as_str()
    };
    let (intent_label_key, command_label, assumptions_label, risk_label) = match language {
        Language::Chinese => ("意图", "命令", "假设", "风险"),
        Language::English => ("Intent", "Command", "Assumptions", "Risk"),
    };
    println!("{intent_label_key}：{intent_label}");
    println!("{command_label}：{}", plan.display_command);
    if options.explain {
        match language {
            Language::Chinese => {
                println!("工具：{}", plan.tool);
                println!("平台：{}", plan.platform.as_str());
            }
            Language::English => {
                println!("Tool: {}", plan.tool);
                println!("Platform: {}", plan.platform.as_str());
            }
        }
    }
    let separator = if language == Language::Chinese {
        "；"
    } else {
        "; "
    };
    println!("{assumptions_label}：{}", plan.assumptions.join(separator));
    println!("{risk_label}：{}", plan.risk.label(language));

    if options.copy {
        match copy_to_clipboard(&plan.display_command, plan.platform) {
            Ok(()) => println!("命令已复制到剪贴板。"),
            Err(message) => {
                match language {
                    Language::Chinese => {
                        eprintln!("复制失败：{message}");
                        println!("请手动复制上面的命令。\n");
                    }
                    Language::English => {
                        eprintln!("Copy failed: {message}");
                        println!("Please copy the command above manually.\n");
                    }
                }
                return ExitCode::from(1);
            }
        }
        return ExitCode::SUCCESS;
    }

    if options.yes {
        if plan.risk == Risk::High {
            match language {
                Language::Chinese => eprintln!("高风险命令不能使用 `--yes` 自动执行，请在交互确认中明确选择。\n"),
                Language::English => eprintln!("High-risk commands cannot be auto-executed with `--yes`; confirm them interactively.\n"),
            }
            return ExitCode::from(2);
        }
        return execute_plan(&plan, language);
    }

    if !io::stdin().is_terminal() {
        match language {
            Language::Chinese => eprintln!("当前不是交互终端；请使用 `--print`、`--copy` 或显式 `--yes`。\n"),
            Language::English => eprintln!("This is not an interactive terminal; use `--print`, `--copy`, or explicit `--yes`.\n"),
        }
        return ExitCode::from(2);
    }

    print_confirmation_help(language);
    let prompt = match language {
        Language::Chinese => "选择 [y/N/e/c]：",
        Language::English => "Choose [y/N/e/c]: ",
    };
    print!("{prompt}");
    let _ = io::stdout().flush();
    let mut answer = String::new();
    if io::stdin().read_line(&mut answer).is_err() {
        match language {
            Language::Chinese => eprintln!("无法读取确认输入，已取消。\n"),
            Language::English => eprintln!("Could not read confirmation; cancelled.\n"),
        }
        return ExitCode::from(1);
    }
    match answer.trim().to_lowercase().as_str() {
        "y" | "yes" => execute_plan(&plan, language),
        "c" => match copy_to_clipboard(&plan.display_command, plan.platform) {
            Ok(()) => {
                match language {
                    Language::Chinese => println!("命令已复制到剪贴板。\n"),
                    Language::English => println!("Command copied to clipboard.\n"),
                }
                ExitCode::SUCCESS
            }
            Err(message) => {
                match language {
                    Language::Chinese => eprintln!("复制失败：{message}"),
                    Language::English => eprintln!("Copy failed: {message}"),
                }
                ExitCode::from(1)
            }
        },
        "e" => {
            match language {
                Language::Chinese => eprintln!("编辑模式将在后续版本提供；当前请复制命令后手动修改。\n"),
                Language::English => eprintln!("Edit mode will arrive in a later version; copy and modify the command manually for now.\n"),
            }
            ExitCode::from(1)
        }
        _ => {
            match language {
                Language::Chinese => println!("已取消。\n"),
                Language::English => println!("Cancelled.\n"),
            }
            ExitCode::SUCCESS
        }
    }
}

fn execute_plan(plan: &IntentPlan, language: Language) -> ExitCode {
    for step in &plan.steps {
        let status = Command::new(&step.program).args(&step.args).status();
        match status {
            Ok(status) if status.success() => {}
            Ok(status) => {
                match language {
                    Language::Chinese => eprintln!(
                        "命令执行失败（退出码 {:?}）：{}",
                        status.code(),
                        plan.display_command
                    ),
                    Language::English => eprintln!(
                        "Command failed (exit code {:?}): {}",
                        status.code(),
                        plan.display_command
                    ),
                }
                return ExitCode::from(status.code().unwrap_or(1) as u8);
            }
            Err(error) => {
                match language {
                    Language::Chinese => eprintln!("无法执行 {}：{error}", step.program),
                    Language::English => eprintln!("Could not execute {}: {error}", step.program),
                }
                return ExitCode::from(1);
            }
        }
    }
    match language {
        Language::Chinese => println!("执行完成。\n"),
        Language::English => println!("Done.\n"),
    }
    ExitCode::SUCCESS
}

fn copy_to_clipboard(command: &str, platform: Platform) -> Result<(), String> {
    let (program, args): (&str, &[&str]) = match platform {
        Platform::Macos => ("pbcopy", &[]),
        Platform::Windows => ("clip.exe", &[]),
        Platform::Linux => ("xclip", &["-selection", "clipboard"]),
        Platform::Other => return Err("当前平台没有内置剪贴板命令".to_string()),
    };
    let mut child = Command::new(program)
        .args(args)
        .stdin(std::process::Stdio::piped())
        .spawn()
        .map_err(|error| format!("{program} 不可用：{error}"))?;
    if let Some(stdin) = child.stdin.as_mut() {
        stdin
            .write_all(command.as_bytes())
            .map_err(|error| format!("写入剪贴板失败：{error}"))?;
    }
    let status = child
        .wait()
        .map_err(|error| format!("等待剪贴板进程失败：{error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{program} 返回退出码 {:?}", status.code()))
    }
}

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

fn load_config(explicit_path: Option<&Path>) -> Config {
    config_from_values(&load_config_values(explicit_path))
}

fn load_config_values(explicit_path: Option<&Path>) -> BTreeMap<String, String> {
    let mut values = BTreeMap::new();
    if let Some(path) = explicit_path {
        values.extend(read_config_map(path));
    } else {
        // Git-like precedence: system < global < local. A missing file is fine.
        if let Some(path) = system_config_path() {
            values.extend(read_config_map(&path));
        }
        if let Some(path) = global_config_path() {
            values.extend(read_config_map(&path));
        }
        values.extend(read_config_map(&local_config_path()));
    }
    values
}

fn config_from_values(values: &BTreeMap<String, String>) -> Config {
    let defaults = Config::default();
    let language = values
        .get("core.language")
        .or_else(|| values.get("language"))
        .and_then(|value| Language::parse(value))
        .unwrap_or(defaults.language);
    Config {
        language,
        provider: values
            .get("llm.provider")
            .cloned()
            .unwrap_or(defaults.provider),
        base_url: values
            .get("llm.base-url")
            .cloned()
            .unwrap_or(defaults.base_url),
        model: values.get("llm.model").cloned().unwrap_or(defaults.model),
        api_key_env: values
            .get("llm.api-key-env")
            .cloned()
            .unwrap_or(defaults.api_key_env),
        timeout_seconds: values
            .get("llm.timeout-seconds")
            .and_then(|value| value.parse().ok())
            .unwrap_or(defaults.timeout_seconds),
    }
}

fn read_config_map(path: &Path) -> BTreeMap<String, String> {
    let Ok(contents) = std::fs::read_to_string(path) else {
        return BTreeMap::new();
    };
    parse_config_contents(&contents)
}

fn parse_config_contents(contents: &str) -> BTreeMap<String, String> {
    let mut values = BTreeMap::new();
    let mut section = String::new();
    for line in contents.lines() {
        let line = line.split('#').next().unwrap_or_default().trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            section = line[1..line.len() - 1].trim().to_lowercase();
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim().to_lowercase();
        let full_key = if section.is_empty() {
            key
        } else {
            format!("{section}.{key}")
        };
        let value = value
            .trim()
            .trim_matches(|character| character == '"' || character == '\'')
            .to_string();
        values.insert(full_key, value);
    }
    values
}

fn system_config_path() -> Option<PathBuf> {
    if cfg!(target_os = "windows") {
        env::var_os("PROGRAMDATA")
            .map(PathBuf::from)
            .map(|path| path.join("dox").join("config"))
    } else {
        Some(PathBuf::from("/etc/doxconfig"))
    }
}

fn global_config_path() -> Option<PathBuf> {
    env::var_os(if cfg!(target_os = "windows") {
        "USERPROFILE"
    } else {
        "HOME"
    })
    .map(PathBuf::from)
    .map(|path| path.join(".doxconfig"))
}

fn local_config_path() -> PathBuf {
    PathBuf::from(".dox").join("config")
}

fn config_scope_path(scope: ConfigScope, explicit: Option<PathBuf>) -> Option<PathBuf> {
    explicit.or_else(|| match scope {
        ConfigScope::System => system_config_path(),
        ConfigScope::Global => global_config_path(),
        ConfigScope::Local => Some(local_config_path()),
    })
}

#[derive(Debug, Clone, Copy)]
enum ConfigScope {
    System,
    Global,
    Local,
}

fn run_config_command(arguments: &[String]) -> ExitCode {
    let mut scope = ConfigScope::Local;
    let mut scope_explicit = false;
    let mut explicit_path = None;
    let mut list = false;
    let mut unset = false;
    let mut positional = Vec::new();
    let mut index = 0;
    while index < arguments.len() {
        match arguments[index].as_str() {
            "--global" => {
                scope = ConfigScope::Global;
                scope_explicit = true;
            }
            "--local" => {
                scope = ConfigScope::Local;
                scope_explicit = true;
            }
            "--system" => {
                scope = ConfigScope::System;
                scope_explicit = true;
            }
            "--list" | "-l" => list = true,
            "--unset" => unset = true,
            "--file" => {
                index += 1;
                let Some(path) = arguments.get(index) else {
                    return config_error("`--file` 需要一个配置文件路径");
                };
                explicit_path = Some(PathBuf::from(path));
            }
            "--help" | "-h" => {
                print_config_usage();
                return ExitCode::SUCCESS;
            }
            value if value.starts_with('-') => {
                return config_error(&format!("未知选项 `{value}`"));
            }
            value => positional.push(value.to_string()),
        }
        index += 1;
    }

    let Some(path) = config_scope_path(scope, explicit_path.clone()) else {
        return config_error("无法确定配置文件路径");
    };
    if list {
        let values = if !scope_explicit && explicit_path.is_none() && path == local_config_path() {
            let mut merged = BTreeMap::new();
            if let Some(system) = system_config_path() {
                merged.extend(read_config_map(&system));
            }
            if let Some(global) = global_config_path() {
                merged.extend(read_config_map(&global));
            }
            merged.extend(read_config_map(&path));
            merged
        } else {
            read_config_map(&path)
        };
        for (key, value) in values {
            println!("{key}={value}");
        }
        return ExitCode::SUCCESS;
    }

    if positional.is_empty() || positional.len() > 2 {
        print_config_usage();
        return ExitCode::from(2);
    }
    let key = canonical_config_key(&positional[0]);
    let mut values = read_config_map(&path);
    if unset {
        values.remove(&key);
    } else if positional.len() == 2 {
        values.insert(key, positional[1].clone());
    } else {
        let values = if scope_explicit || explicit_path.is_some() {
            read_config_map(&path)
        } else {
            load_config_values(None)
        };
        if let Some(value) = values.get(&key) {
            println!("{value}");
            return ExitCode::SUCCESS;
        }
        return config_error("配置项不存在");
    }
    if let Some(parent) = path.parent() {
        if let Err(error) = std::fs::create_dir_all(parent) {
            return config_error(&format!("无法创建配置目录：{error}"));
        }
    }
    if let Err(error) = std::fs::write(&path, render_config(&values)) {
        return config_error(&format!("无法写入配置文件 {}：{error}", path.display()));
    }
    ExitCode::SUCCESS
}

fn canonical_config_key(key: &str) -> String {
    match key {
        "language" => "core.language".to_string(),
        "provider" => "llm.provider".to_string(),
        "base-url" | "api.base-url" => "llm.base-url".to_string(),
        "model" => "llm.model".to_string(),
        "api-key-env" => "llm.api-key-env".to_string(),
        "timeout" | "timeout-seconds" => "llm.timeout-seconds".to_string(),
        value => value.to_lowercase(),
    }
}

fn render_config(values: &BTreeMap<String, String>) -> String {
    let mut sections = BTreeMap::<String, BTreeMap<String, String>>::new();
    for (key, value) in values {
        let (section, name) = key.split_once('.').unwrap_or(("core", key));
        sections
            .entry(section.to_string())
            .or_default()
            .insert(name.to_string(), value.to_string());
    }
    let mut output = String::new();
    for (section, entries) in sections {
        output.push_str(&format!("[{section}]\n"));
        for (key, value) in entries {
            output.push_str(&format!("{key} = \"{}\"\n", value.replace('"', "\\\"")));
        }
        output.push('\n');
    }
    output
}

fn config_error(message: &str) -> ExitCode {
    eprintln!("dox config：{message}");
    ExitCode::from(2)
}

fn print_config_usage() {
    println!(
        "用法：\n  dox config [--global|--local|--system|--file PATH] KEY VALUE\n  dox config [--global|--local|--system|--file PATH] KEY\n  dox config --list\n  dox config --unset KEY\n\n示例：\n  dox config --global llm.provider openai-compatible\n  dox config --global llm.base-url https://api.openai.com/v1\n  dox config --global llm.model gpt-4o-mini\n  dox config --global llm.api-key-env OPENAI_API_KEY\n  dox config --global core.language zh"
    );
}

fn print_confirmation_help(language: Language) {
    match language {
        Language::Chinese => println!(
            "\n执行？\n  y 执行\n  N 取消（默认）\n  e 编辑命令（当前版本暂未实现）\n  c 复制命令"
        ),
        Language::English => println!(
            "\nExecute?\n  y execute\n  N cancel (default)\n  e edit command (not available yet)\n  c copy command"
        ),
    }
}

fn print_usage(language: Language) {
    match language {
        Language::Chinese => println!(
            "dox 0.1.0\n\n用法：\n  dox \"自然语言命令\" [选项]\n\n选项：\n  --print       只打印命令，不执行\n  --copy        复制命令，不执行\n  --json        输出 IntentPlan JSON\n  --yes         跳过普通确认\n  --explain     显示额外的计划信息\n  --lang zh|en  设置界面语言（默认 zh）\n  --config PATH 使用指定配置文件\n  --offline     只使用离线能力（本地 Provider 尚未接入）\n  --local       请求本地模型（当前版本尚未接入）\n  --api         强制使用 API LLM\n  -h, --help    显示帮助\n\n当前 P0 能力：通过 API LLM 生成结构化命令计划。"
        ),
        Language::English => println!(
            "dox 0.1.0\n\nUsage:\n  dox \"natural-language request\" [options]\n\nOptions:\n  --print       print the command only\n  --copy        copy the command without executing\n  --json        output an IntentPlan JSON\n  --yes         skip ordinary confirmation\n  --explain     show extra plan details\n  --lang zh|en  set interface language (default: zh)\n  --config PATH use a specific config file\n  --offline     use offline capabilities only (local provider is not available yet)\n  --local       request the local model (not available yet)\n  --api         force the API LLM provider\n  -h, --help    show this help\n\nCurrent P0 capability: structured command planning through an API LLM."
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_model_plan() {
        let content = r#"{"intent":"file.find","command":"find . -type f -name '*.log'","shell":"zsh","risk":"read_only","assumptions":["当前目录为搜索根目录"],"tools":["find"],"clarification":null}"#;
        let outcome = parse_model_plan(content, Platform::Macos, Language::Chinese)
            .expect("model plan should parse");
        let PlanOutcome::Plan(plan) = outcome else {
            panic!("expected a plan");
        };
        assert_eq!(plan.intent, "file.find");
        assert_eq!(plan.risk, Risk::ReadOnly);
        assert_eq!(plan.steps[0].program, "zsh");
    }

    #[test]
    fn escalates_dangerous_command_risk() {
        let content = r#"{"intent":"file.delete","command":"rm -rf ./build","shell":"zsh","risk":"write","assumptions":[],"tools":["rm"],"clarification":null}"#;
        let outcome = parse_model_plan(content, Platform::Macos, Language::Chinese)
            .expect("model plan should parse");
        let PlanOutcome::Plan(plan) = outcome else {
            panic!("expected a plan");
        };
        assert_eq!(plan.risk, Risk::High);
    }

    #[test]
    fn returns_clarification_from_model() {
        let content = r#"{"intent":"file.find","command":"","shell":"zsh","risk":"read_only","assumptions":[],"tools":["find"],"clarification":"Which directory should be searched?"}"#;
        let outcome = parse_model_plan(content, Platform::Linux, Language::English)
            .expect("clarification should parse");
        assert!(matches!(
            outcome,
            PlanOutcome::Clarification(message) if message == "Which directory should be searched?"
        ));
    }

    #[test]
    fn rejects_unsupported_shell() {
        let content = r#"{"intent":"file.find","command":"dir","shell":"cmd","risk":"read_only","assumptions":[],"tools":["dir"],"clarification":null}"#;
        let error = parse_model_plan(content, Platform::Windows, Language::English)
            .expect_err("cmd should not be accepted on Windows");
        assert!(error.contains("Shell"));
    }

    #[test]
    fn parses_git_style_config() {
        let values = parse_config_contents(
            "[core]\nlanguage = en\n[llm]\nmodel = demo\nbase-url = https://example.test/v1\n",
        );
        let config = config_from_values(&values);
        assert_eq!(config.language, Language::English);
        assert_eq!(config.model, "demo");
        assert_eq!(config.base_url, "https://example.test/v1");
    }

    #[test]
    fn asks_for_model_when_not_configured() {
        let config = Config::default();
        let error = plan_with_api("list files", Platform::Macos, Language::Chinese, &config)
            .expect_err("missing model should be reported before network");
        assert!(error.contains("模型"));
    }

    #[test]
    fn config_key_aliases_are_canonicalized() {
        assert_eq!(canonical_config_key("language"), "core.language");
        assert_eq!(canonical_config_key("model"), "llm.model");
        assert_eq!(canonical_config_key("base-url"), "llm.base-url");
    }

    #[test]
    fn default_config_is_chinese() {
        assert_eq!(Config::default().language, Language::Chinese);
    }

    #[test]
    fn shell_command_is_wrapped_for_posix() {
        let steps = build_model_steps(Platform::Linux, "/bin/bash", "printf hello");
        assert_eq!(steps[0].program, "/bin/bash");
        assert_eq!(steps[0].args, vec!["-lc", "printf hello"]);
    }

    #[test]
    fn model_json_handles_fenced_output() {
        let content = "```json\n{\"intent\":\"system.info\",\"command\":\"uname -a\",\"shell\":\"sh\",\"risk\":\"read_only\",\"assumptions\":[],\"tools\":[\"uname\"],\"clarification\":null}\n```";
        let outcome = parse_model_plan(content, Platform::Linux, Language::Chinese)
            .expect("fenced JSON should parse");
        assert!(matches!(outcome, PlanOutcome::Plan(_)));
    }

    #[test]
    fn unknown_model_risk_defaults_high() {
        let content = r#"{"intent":"unknown","command":"custom-tool --flag","shell":"sh","risk":"unknown","assumptions":[],"tools":[],"clarification":null}"#;
        let outcome = parse_model_plan(content, Platform::Linux, Language::English)
            .expect("command should parse");
        let PlanOutcome::Plan(plan) = outcome else {
            panic!("expected a plan");
        };
        assert_eq!(plan.risk, Risk::High);
    }

    #[test]
    fn model_command_rejects_control_characters() {
        let content = "{\"intent\":\"x\",\"command\":\"printf hello\\nworld\",\"shell\":\"sh\",\"risk\":\"read_only\",\"assumptions\":[],\"tools\":[],\"clarification\":null}";
        let error = parse_model_plan(content, Platform::Linux, Language::English)
            .expect_err("newline should be rejected");
        assert!(error.contains("控制字符"));
    }

    #[test]
    fn config_render_round_trips_values() {
        let mut values = BTreeMap::new();
        values.insert("core.language".to_string(), "en".to_string());
        values.insert("llm.model".to_string(), "demo".to_string());
        let rendered = render_config(&values);
        let reparsed = parse_config_contents(&rendered);
        assert_eq!(reparsed, values);
    }

    #[test]
    fn extracts_platform_shell() {
        assert!(shell_supported(Platform::Windows, "powershell"));
        assert!(!shell_supported(Platform::Windows, "cmd"));
        assert!(shell_supported(Platform::Linux, "bash"));
    }

    #[test]
    fn endpoint_builder_handles_base_and_full_url() {
        assert_eq!(
            chat_endpoint("https://example.test/v1"),
            "https://example.test/v1/chat/completions"
        );
        assert_eq!(
            chat_endpoint("https://example.test/v1/chat/completions"),
            "https://example.test/v1/chat/completions"
        );
    }

    #[test]
    fn parse_risk_aliases() {
        assert_eq!(parse_risk("read-only"), Some(Risk::ReadOnly));
        assert_eq!(parse_risk("dangerous"), Some(Risk::High));
    }

    #[test]
    fn config_scope_defaults_to_local_path() {
        assert_eq!(
            config_scope_path(ConfigScope::Local, None),
            Some(local_config_path())
        );
    }

    #[test]
    fn model_plan_defaults_tool_to_shell() {
        let content = r#"{"intent":"x","command":"echo ok","shell":"sh","risk":"read_only","assumptions":[],"clarification":null}"#;
        let outcome = parse_model_plan(content, Platform::Linux, Language::English)
            .expect("model plan should parse");
        let PlanOutcome::Plan(plan) = outcome else {
            panic!("expected a plan");
        };
        assert_eq!(plan.tool, "shell");
    }
}
