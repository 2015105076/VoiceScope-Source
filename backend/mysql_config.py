"""Credential-safe MySQL settings with separate fields preferred over URL strings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class MySQLSettings:
    host: str | None
    port: int
    user: str | None
    password: str
    database: str | None
    source: str

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.user is not None and self.database)

    def public(self) -> dict[str, object]:
        return {"configured": self.enabled, "host": self.host, "port": self.port if self.enabled else None,
                "database": self.database, "source": self.source}


def load_mysql_settings() -> MySQLSettings:
    host = os.environ.get("VOICESCOPE_MYSQL_HOST", "").strip()
    user = os.environ.get("VOICESCOPE_MYSQL_USER")
    database = os.environ.get("VOICESCOPE_MYSQL_DATABASE", "").strip()
    password = os.environ.get("VOICESCOPE_MYSQL_PASSWORD")
    port_text = os.environ.get("VOICESCOPE_MYSQL_PORT", "3306")
    if host or user is not None or database or password is not None:
        try:
            port = int(port_text)
        except ValueError as error:
            raise RuntimeError("VOICESCOPE_MYSQL_PORT 必须是整数") from error
        return MySQLSettings(host or None, port, user, password or "", database or None, "separate_environment")
    url = os.environ.get("VOICESCOPE_MYSQL_URL", "").strip()
    if not url:
        return MySQLSettings(None, 3306, None, "", None, "disabled")
    try:
        parsed = urlsplit(url)
        port = parsed.port or 3306
    except ValueError as error:
        raise RuntimeError("VOICESCOPE_MYSQL_URL 格式无效；建议改用分开的 VOICESCOPE_MYSQL_HOST/USER/PASSWORD/DATABASE") from error
    if parsed.scheme not in {"mysql", "mysql+pymysql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError("VOICESCOPE_MYSQL_URL 应为 mysql://用户:密码@主机:端口/数据库")
    return MySQLSettings(parsed.hostname, port, unquote(parsed.username or ""), unquote(parsed.password or ""), unquote(parsed.path.strip("/")), "url")
