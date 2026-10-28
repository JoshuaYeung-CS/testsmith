from testsmith.config import DEFAULT_BASE_URL, DEFAULT_JUDGE_MODEL, DEFAULT_MODEL, Settings, load_dotenv, parse_prices


def test_defaults_are_the_documented_token_factory_values():
    s = Settings.from_env(env={})
    assert s.base_url == "https://api.tokenfactory.nebius.com/v1/" == DEFAULT_BASE_URL
    assert s.model == "nvidia/Nemotron-3_5-Lightning" == DEFAULT_MODEL
    assert s.judge_model == "nvidia/Nemotron-3-Ultra-550b-a55b" == DEFAULT_JUDGE_MODEL
    assert s.api_key is None


def test_env_overrides(tmp_path):
    s = Settings.from_env(
        env={
            "NEBIUS_API_KEY": "k",
            "NEBIUS_BASE_URL": "http://localhost:1/v1/",
            "TESTSMITH_MODEL": "nvidia/other",
            "TESTSMITH_JUDGE_MODEL": "nvidia/judge",
            "TESTSMITH_MAX_TOKENS": "100",
        }
    )
    assert (s.api_key, s.base_url, s.model, s.judge_model, s.max_tokens) == (
        "k",
        "http://localhost:1/v1/",
        "nvidia/other",
        "nvidia/judge",
        100,
    )


def test_dotenv_is_read_and_real_env_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nexport NEBIUS_API_KEY="from-file"\nTESTSMITH_MODEL=file-model  # note\n\n')
    assert load_dotenv(env_file) == {"NEBIUS_API_KEY": "from-file", "TESTSMITH_MODEL": "file-model"}
    s = Settings.from_env(env={"TESTSMITH_MODEL": "env-model"}, dotenv=env_file)
    assert s.api_key == "from-file"
    assert s.model == "env-model"


def test_blank_values_fall_back_to_defaults():
    s = Settings.from_env(env={"TESTSMITH_MODEL": "  ", "NEBIUS_BASE_URL": ""})
    assert s.model == DEFAULT_MODEL and s.base_url == DEFAULT_BASE_URL


def test_price_overrides():
    assert parse_prices("a/b=0.1/0.4, bad, c=x/y") == {"a/b": (0.1, 0.4)}
    s = Settings.from_env(env={"TESTSMITH_PRICES": "nvidia/Nemotron-3_5-Lightning=0.05/0.2"})
    assert s.prices["nvidia/Nemotron-3_5-Lightning"] == (0.05, 0.2)
    assert s.prices["nvidia/Nemotron-3-Ultra-550b-a55b"] == (1.0, 3.0)
