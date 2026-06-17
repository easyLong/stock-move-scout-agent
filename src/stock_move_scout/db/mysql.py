from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MYSQL_EXE = r"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe"


@dataclass
class MySqlConfig:
    mysql_exe: str = DEFAULT_MYSQL_EXE
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "stock_scout"
    timeout: int = 30


def run_mysql(config: MySqlConfig, sql: str, *, database: bool = True, batch: bool = False, raw: bool = False) -> str:
    mysql_exe = Path(config.mysql_exe)
    if not mysql_exe.exists():
        raise FileNotFoundError(f"mysql.exe not found: {config.mysql_exe}")
    command = [
        str(mysql_exe),
        f"--host={config.host}",
        f"--port={config.port}",
        f"--user={config.user}",
        "--default-character-set=utf8mb4",
        "--binary-mode",
    ]
    if database:
        command.append(f"--database={config.database}")
    if batch:
        command.extend(["--batch", "--skip-column-names"])
    if raw:
        command.append("--raw")
    env = os.environ.copy()
    if config.password:
        env["MYSQL_PWD"] = config.password
    # Feed bytes to mysql.exe to avoid Windows text-mode / encoding quirks.
    # We still request utf8mb4 on the client side via --default-character-set.
    input_bytes = (sql or "").encode("utf-8", errors="replace")
    result = subprocess.run(
        command,
        input=input_bytes,
        capture_output=True,
        timeout=config.timeout,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        tail = (stderr or stdout)[-3000:]
        raise RuntimeError(tail.strip())
    return (result.stdout or b"").decode("utf-8", errors="replace").strip()


def mysql_rows(output: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in output.splitlines():
        if line.strip():
            rows.append(line.split("\t"))
    return rows


def mysql_config_from_args(args: argparse.Namespace) -> MySqlConfig:
    return MySqlConfig(
        mysql_exe=str(args.mysql_exe),
        host=str(args.mysql_host),
        port=int(args.mysql_port),
        user=str(args.mysql_user),
        password=str(args.mysql_password or os.environ.get("MYSQL_PWD", "")),
        database=str(args.mysql_database),
        timeout=int(args.mysql_timeout),
    )


def mysql_cli_args_from_args(args: argparse.Namespace, *, include_enabled: bool = True) -> list[str]:
    cli_args: list[str] = []
    if include_enabled:
        cli_args.append("--mysql-enabled")
    cli_args.extend(
        [
            "--mysql-exe",
            str(args.mysql_exe),
            "--mysql-host",
            str(args.mysql_host),
            "--mysql-port",
            str(args.mysql_port),
            "--mysql-user",
            str(args.mysql_user),
            "--mysql-database",
            str(args.mysql_database),
            "--mysql-timeout",
            str(args.mysql_timeout),
        ]
    )
    if args.mysql_password:
        cli_args.extend(["--mysql-password", str(args.mysql_password)])
    return cli_args


def add_mysql_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mysql-enabled", action="store_true")
    parser.add_argument("--mysql-exe", default=os.environ.get("MYSQL_EXE", DEFAULT_MYSQL_EXE))
    parser.add_argument("--mysql-host", default=os.environ.get("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", type=int, default=int(os.environ.get("MYSQL_PORT", "3306")))
    parser.add_argument("--mysql-user", default=os.environ.get("MYSQL_USER", "root"))
    parser.add_argument("--mysql-password", default=os.environ.get("MYSQL_PWD", ""))
    parser.add_argument("--mysql-database", default=os.environ.get("MYSQL_DATABASE", "stock_scout"))
    parser.add_argument("--mysql-timeout", type=int, default=int(os.environ.get("MYSQL_TIMEOUT", "30")))
