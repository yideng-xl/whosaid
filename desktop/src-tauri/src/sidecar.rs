//! Python 转写服务的进程管理：启动 `python -m transcribe_core.server`，
//! 读它 stdout 的 `PORT=<n>` 完成握手；解析函数拆成纯函数便于单测。
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

/// 从服务 stdout 的一行里解析 `PORT=<n>`。非该格式返回 None。
pub fn parse_port(line: &str) -> Option<u16> {
    line.strip_prefix("PORT=")?.trim().parse::<u16>().ok()
}

/// 启动 `python -m transcribe_core.server` 并等待其打印 `PORT=<n>`，约定超时 30s。
/// cwd 设为数据目录（内核在此落 config.json 与持久化数据）；pythonpath 指向 core 根，
/// 让未 pip 安装的 transcribe_core 可被导入。同时透传数据目录与 HF 镜像环境变量。
/// 返回子进程句柄与端口，供外壳持有/退出时 kill。
pub fn spawn_service(python: &str, cwd: &str, pythonpath: &str) -> std::io::Result<(Child, u16)> {
    spawn_service_with_timeout(
        python,
        cwd,
        pythonpath,
        Duration::from_secs(30),
        &["-m", "transcribe_core.server"],
    )
}

/// `spawn_service` 的可测版本：可注入超时时长与命令 args（测试用 `/bin/sleep` 等假服务）。
/// 用后台线程逐行读 stdout 找 `PORT=`，主线程通过 `mpsc::recv_timeout` 等待；
/// 超时、子进程提前退出（EOF）均视为失败并 kill 子进程，避免 setup 无限阻塞卡首屏。
pub fn spawn_service_with_timeout(
    python: &str,
    cwd: &str,
    pythonpath: &str,
    timeout: Duration,
    args: &[&str],
) -> std::io::Result<(Child, u16)> {
    let mut child = Command::new(python)
        .args(args)
        .current_dir(cwd)
        .env("PYTHONPATH", pythonpath)
        .env("WHOSAID_DATA_DIR", cwd)
        .env("HF_ENDPOINT", "https://hf-mirror.com")
        .stdout(Stdio::piped())
        .spawn()?;
    let stdout = child.stdout.take().expect("stdout piped");
    let (tx, rx) = mpsc::channel::<Option<u16>>();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => {
                    // 读到 EOF 或读取出错：服务异常退出，通知主线程失败
                    let _ = tx.send(None);
                    return;
                }
                Ok(_) => {
                    if let Some(p) = parse_port(line.trim_end()) {
                        let _ = tx.send(Some(p));
                        return;
                    }
                }
            }
        }
    });
    match rx.recv_timeout(timeout) {
        Ok(Some(port)) => Ok((child, port)),
        _ => {
            // 超时或子进程提前退出：kill 掉子进程（避免残留），返回错误
            child.kill().ok();
            Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "服务未在超时内打印 PORT",
            ))
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

    #[test]
    fn spawn_times_out_when_no_port() {
        // sleep 5 秒且不打印 PORT 的假服务：用极短超时应返回 Err
        let r = spawn_service_with_timeout(
            "/bin/sleep",
            ".",
            ".",
            Duration::from_millis(300),
            &["2"],
        );
        assert!(r.is_err());
    }
}
