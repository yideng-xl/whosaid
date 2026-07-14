//! Python 转写服务的进程管理：启动 `python -m transcribe_core.server`，
//! 读它 stdout 的 `PORT=<n>` 完成握手；解析函数拆成纯函数便于单测。
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};

/// 从服务 stdout 的一行里解析 `PORT=<n>`。非该格式返回 None。
pub fn parse_port(line: &str) -> Option<u16> {
    line.strip_prefix("PORT=")?.trim().parse::<u16>().ok()
}

/// 启动 `python -m transcribe_core.server`，逐行读 stdout 直到拿到端口。
/// cwd 设为数据目录（内核在此落 config.json 与持久化数据），并透传数据目录
/// 与 HF 镜像环境变量。返回子进程句柄与端口，供外壳持有/退出时 kill。
pub fn spawn_service(python: &str, cwd: &str) -> std::io::Result<(Child, u16)> {
    let mut child = Command::new(python)
        .args(["-m", "transcribe_core.server"])
        .current_dir(cwd)
        .env("WHOSAID_DATA_DIR", cwd)
        .env("HF_ENDPOINT", "https://hf-mirror.com")
        .stdout(Stdio::piped())
        .spawn()?;
    let stdout = child.stdout.take().expect("stdout piped");
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    loop {
        line.clear();
        if reader.read_line(&mut line)? == 0 {
            // 读到 EOF 仍未见 PORT：服务异常退出，返回错误
            return Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                "服务未打印 PORT 即退出",
            ));
        }
        if let Some(port) = parse_port(line.trim_end()) {
            return Ok((child, port));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_port_line() {
        assert_eq!(parse_port("PORT=63179"), Some(63179));
    }

    #[test]
    fn ignores_other_lines() {
        assert_eq!(parse_port("INFO: started"), None);
        assert_eq!(parse_port("PORT=abc"), None);
    }
}
