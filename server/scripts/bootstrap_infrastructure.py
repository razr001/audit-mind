from __future__ import annotations

import base64
import getpass
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE_FILE = ROOT / "docker-compose.yml"
KIBANA_ENV_FILE = ROOT / "docker" / "kibana" / ".env"
KIBANA_ENV_EXAMPLE = ROOT / "docker" / "kibana" / ".env.example"
ELASTICSEARCH_ENV_FILE = ROOT / "docker" / "elasticsearch" / ".env"
ELASTICSEARCH_ENV_EXAMPLE = (
	ROOT / "docker" / "elasticsearch" / ".env.example"
)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		args,
		cwd=ROOT,
		check=check,
		text=True,
	)


def read_env(path: Path) -> dict[str, str]:
	values: dict[str, str] = {}
	if not path.exists():
		return values

	for raw_line in path.read_text(encoding="utf-8").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, value = line.split("=", 1)
		values[key.strip()] = value.strip()
	return values


def update_env(path: Path, updates: dict[str, str]) -> None:
	lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
	remaining = dict(updates)
	result: list[str] = []

	for line in lines:
		stripped = line.strip()
		if stripped and not stripped.startswith("#") and "=" in stripped:
			key = stripped.split("=", 1)[0].strip()
			if key in remaining:
				result.append(f"{key}={remaining.pop(key)}")
				continue
		result.append(line)

	if remaining:
		if result and result[-1] != "":
			result.append("")
		result.extend(f"{key}={value}" for key, value in remaining.items())

	temporary = path.with_suffix(".tmp")
	temporary.write_text("\n".join(result) + "\n", encoding="utf-8")
	temporary.replace(path)


def is_placeholder(value: str | None) -> bool:
	if not value:
		return True
	lowered = value.lower()
	return lowered.startswith("your") or "changeme" in lowered


def prompt_password(label: str, current: str | None) -> str:
	if not is_placeholder(current):
		return current or ""

	while True:
		value = getpass.getpass(f"Set {label} (minimum 8 characters): ")
		confirmation = getpass.getpass(f"Confirm {label}: ")
		if value != confirmation:
			print("Passwords do not match. Try again.")
			continue
		if len(value) < 8:
			print("Password must contain at least 8 characters.")
			continue
		return value


def wait_for_elasticsearch(password: str, timeout: int = 240) -> None:
	authorization = base64.b64encode(
		f"elastic:{password}".encode("utf-8")
	).decode("ascii")
	deadline = time.monotonic() + timeout

	while time.monotonic() < deadline:
		request = urllib.request.Request(
			"http://localhost:9200/_cluster/health",
			headers={"Authorization": f"Basic {authorization}"},
		)
		try:
			with urllib.request.urlopen(request, timeout=5) as response:
				if response.status == 200:
					return
		except (urllib.error.URLError, TimeoutError):
			pass
		time.sleep(3)

	raise TimeoutError("Elasticsearch did not become healthy in time")


def set_kibana_system_password(elastic_password: str, kibana_password: str) -> None:
	authorization = base64.b64encode(
		f"elastic:{elastic_password}".encode("utf-8")
	).decode("ascii")
	body = json.dumps({"password": kibana_password}).encode("utf-8")
	request = urllib.request.Request(
		"http://localhost:9200/_security/user/kibana_system/_password",
		data=body,
		method="POST",
		headers={
			"Authorization": f"Basic {authorization}",
			"Content-Type": "application/json",
		},
	)
	with urllib.request.urlopen(request, timeout=15) as response:
		if response.status not in (200, 201):
			raise RuntimeError("Failed to configure kibana_system password")


def create_auditmind_api_key(elastic_password: str) -> str:
	"""创建仅能访问 AuditMind 业务索引的 FastAPI API Key。"""
	authorization = base64.b64encode(
		f"elastic:{elastic_password}".encode("utf-8")
	).decode("ascii")
	body = json.dumps(
		{
			"name": "auditmind-api",
			"role_descriptors": {
				"auditmind_runtime": {
					"cluster": [],
					"indices": [
						{
							"names": [
								"auditmind-document-chunks-*",
								"auditmind-regulation-chunks-*",
								"auditmind-regulation-chunks",
								"auditmind-regulation-rules-*",
								"auditmind-regulation-rules",
							],
							"privileges": [
								"read",
								"write",
								"create_index",
								"manage",
							],
						}
					],
				}
			},
		}
	).encode("utf-8")
	request = urllib.request.Request(
		"http://localhost:9200/_security/api_key",
		data=body,
		method="POST",
		headers={
			"Authorization": f"Basic {authorization}",
			"Content-Type": "application/json",
		},
	)
	with urllib.request.urlopen(request, timeout=15) as response:
		payload = json.load(response)
	encoded = payload.get("encoded")
	if not isinstance(encoded, str) or not encoded:
		raise RuntimeError("Elasticsearch did not return an encoded API key")
	return encoded


def wait_for_port(host: str, port: int, label: str, timeout: int = 240) -> None:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		try:
			with socket.create_connection((host, port), timeout=3):
				return
		except OSError:
			time.sleep(3)
	raise TimeoutError(f"{label} did not become available in time")


def main() -> int:
	if shutil.which("docker") is None:
		print("Docker is not installed or is not available on PATH.", file=sys.stderr)
		return 1
	if not COMPOSE_FILE.exists():
		print(f"Missing compose file: {COMPOSE_FILE}", file=sys.stderr)
		return 1
	if not ENV_FILE.exists():
		if not ENV_EXAMPLE.exists():
			print("Neither .env nor .env.example exists.", file=sys.stderr)
			return 1
		shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
		print("Created .env from .env.example.")

	values = read_env(ENV_FILE)
	if not ELASTICSEARCH_ENV_FILE.exists():
		ELASTICSEARCH_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
		if ELASTICSEARCH_ENV_EXAMPLE.exists():
			shutil.copyfile(
				ELASTICSEARCH_ENV_EXAMPLE,
				ELASTICSEARCH_ENV_FILE,
			)
	elasticsearch_values = read_env(ELASTICSEARCH_ENV_FILE)
	if not KIBANA_ENV_FILE.exists():
		KIBANA_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
		if KIBANA_ENV_EXAMPLE.exists():
			shutil.copyfile(KIBANA_ENV_EXAMPLE, KIBANA_ENV_FILE)
	kibana_values = read_env(KIBANA_ENV_FILE)
	elastic_password = prompt_password(
		"ELASTIC_PASSWORD",
		elasticsearch_values.get("ELASTIC_PASSWORD"),
	)
	kibana_password = prompt_password(
		"KIBANA_SYSTEM_PASSWORD",
		kibana_values.get("ELASTICSEARCH_PASSWORD"),
	)
	update_env(
		ELASTICSEARCH_ENV_FILE,
		{"ELASTIC_PASSWORD": elastic_password},
	)
	update_env(
		KIBANA_ENV_FILE,
		{"ELASTICSEARCH_PASSWORD": kibana_password},
	)

	print("Validating Docker Compose configuration...")
	run("docker", "compose", "config", "--quiet")

	try:
		print("Starting Elasticsearch...")
		run(
			"docker",
			"compose",
			"up",
			"-d",
			"--build",
			"elasticsearch",
		)
		wait_for_elasticsearch(elastic_password)

		if is_placeholder(values.get("ELASTICSEARCH_API_KEY")):
			print("Creating the FastAPI Elasticsearch API key...")
			api_key = create_auditmind_api_key(elastic_password)
			update_env(ENV_FILE, {"ELASTICSEARCH_API_KEY": api_key})

		print("Configuring Kibana system credentials...")
		set_kibana_system_password(elastic_password, kibana_password)

		print("Starting Kibana...")
		run("docker", "compose", "up", "-d", "kibana")
		wait_for_port("localhost", 5601, "Kibana")
	except Exception as exc:
		print(f"Bootstrap failed: {exc}", file=sys.stderr)
		run(
			"docker",
			"compose",
			"logs",
			"--tail",
			"100",
			"elasticsearch",
			"kibana",
			check=False,
		)
		return 1

	print("Infrastructure is ready:")
	print("  Elasticsearch: http://localhost:9200")
	print("  Kibana:        http://localhost:5601")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
