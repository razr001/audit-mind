from __future__ import annotations

import http.client
import json
import shutil
import sys
import time
import urllib.error
import urllib.request

from bootstrap_infrastructure import (
	COMPOSE_FILE,
	ELASTICSEARCH_ENV_FILE,
	ENV_FILE,
	KIBANA_ENV_EXAMPLE,
	KIBANA_ENV_FILE,
	prompt_password,
	read_env,
	run,
	set_kibana_system_password,
	update_env,
)

KIBANA_STATUS_URL = "http://localhost:5601/api/status"


def wait_for_kibana(timeout: int = 240) -> None:
	"""等待 Kibana 完成 Elasticsearch 连接和内部索引初始化。"""
	deadline = time.monotonic() + timeout

	while time.monotonic() < deadline:
		try:
			with urllib.request.urlopen(
				KIBANA_STATUS_URL,
				timeout=5,
			) as response:
				payload = json.load(response)
				level = payload.get("status", {}).get("overall", {}).get(
					"level"
				)
				if level == "available":
					return
		except (
			http.client.RemoteDisconnected,
			json.JSONDecodeError,
			OSError,
			TimeoutError,
			urllib.error.URLError,
		):
			# 启动期间端口可能尚未监听，也可能暂时返回 503。
			pass
		time.sleep(3)

	raise TimeoutError("Kibana did not become available in time")


def main() -> int:
	"""配置 kibana_system 密码，重建 Kibana 并验证服务状态。"""
	if shutil.which("docker") is None:
		print(
			"Docker is not installed or is not available on PATH.",
			file=sys.stderr,
		)
		return 1
	if not COMPOSE_FILE.exists():
		print(f"Missing compose file: {COMPOSE_FILE}", file=sys.stderr)
		return 1
	if not ENV_FILE.exists():
		print(f"Missing environment file: {ENV_FILE}", file=sys.stderr)
		return 1

	elasticsearch_values = read_env(ELASTICSEARCH_ENV_FILE)
	elastic_password = elasticsearch_values.get("ELASTIC_PASSWORD")
	if not elastic_password:
		# 这里不能自动生成管理员密码：它必须与正在运行的 Elasticsearch
		# 中 elastic 用户的真实密码完全一致。
		print(
			"ELASTIC_PASSWORD is missing from docker/elasticsearch/.env.",
			file=sys.stderr,
		)
		return 1

	# Kibana 的凭据写入独立基础设施文件，不能混入 FastAPI 的 .env。
	if not KIBANA_ENV_FILE.exists():
		KIBANA_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
		if KIBANA_ENV_EXAMPLE.exists():
			shutil.copyfile(KIBANA_ENV_EXAMPLE, KIBANA_ENV_FILE)
	kibana_values = read_env(KIBANA_ENV_FILE)

	# kibana_system 是 Kibana 的内部服务用户。缺少密码时安全地提示输入，
	# 已配置时直接复用，整个过程都不会把密码输出到终端。
	kibana_password = prompt_password(
		"KIBANA_SYSTEM_PASSWORD",
		kibana_values.get("ELASTICSEARCH_PASSWORD"),
	)
	update_env(
		KIBANA_ENV_FILE,
		{"ELASTICSEARCH_PASSWORD": kibana_password},
	)

	try:
		print("Configuring the kibana_system password in Elasticsearch...")
		set_kibana_system_password(
			elastic_password,
			kibana_password,
		)

		# 必须重建而不是只 restart；restart 不会重新读取 Compose 环境变量。
		print("Recreating Kibana with the updated credentials...")
		run(
			"docker",
			"compose",
			"up",
			"-d",
			"--force-recreate",
			"kibana",
		)

		print("Waiting for Kibana to become available...")
		wait_for_kibana()
	except urllib.error.HTTPError as exc:
		if exc.code == 401:
			message = (
				"Elasticsearch rejected ELASTIC_PASSWORD in "
				"docker/elasticsearch/.env."
			)
		else:
			message = f"Elasticsearch returned HTTP {exc.code}."
		print(f"Kibana setup failed: {message}", file=sys.stderr)
		return 1
	except Exception as exc:
		print(f"Kibana setup failed: {exc}", file=sys.stderr)
		run(
			"docker",
			"compose",
			"logs",
			"--tail",
			"100",
			"kibana",
			check=False,
		)
		return 1

	print("Kibana is available at http://localhost:5601")
	print("Sign in with the elastic user, not kibana_system.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
