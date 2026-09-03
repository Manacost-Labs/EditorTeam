from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_gateway_dockerfile_pins_dictionary_and_vale_for_both_architectures() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "RU_DICT_VERSION=1.0.8" in dockerfile
    assert "b3a4672933b957258be74c6c46e016c83e8e9c796259a08c00f8fd52ebed2d97" in dockerfile
    assert "VALE_VERSION=3.17.0" in dockerfile
    assert "a903f1f60c3293fac643e0137f599a462881cc691ee19d6120dcfc786f1be86d" in dockerfile
    assert "c7da52f10d25fb97e14370b2f77ac5ebdbd23cf0abc156659463cfa785282692" in dockerfile
    assert '"amd64"' in dockerfile
    assert '"arm64"' in dockerfile
    assert "sha256sum -c" in dockerfile
    assert "apk add --no-cache ca-certificates curl gcompat libstdc++ tar unzip" in dockerfile
    assert "apk add --no-cache ca-certificates gcompat libstdc++ nodejs npm hunspell" in dockerfile


def test_gateway_compose_uses_installed_dictionary_and_vale_config() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "RU_DICT_PATH: ${RU_DICT_PATH:-/usr/share/hunspell/ru_RU.dic}" in compose
    assert "VALE_CONFIG: ${VALE_CONFIG:-/app/.vale.ini}" in compose


def test_ci_runs_the_standard_compose_health_path() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "docker buildx build --platform linux/amd64,linux/arm64 --target gateway ." in workflow
    for command in (
        "docker compose config",
        "docker compose build",
        "docker compose up -d --wait",
        "curl --fail http://127.0.0.1:8740/health",
        "docker compose down",
    ):
        assert command in workflow
