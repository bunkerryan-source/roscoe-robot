from bot.midjourney import extract_params


def test_extract_params_returns_empty_when_no_flags():
    assert extract_params("just a regular sentence") == {}


def test_extract_params_returns_empty_on_none_or_empty_string():
    assert extract_params("") == {}
    assert extract_params(None) == {}


def test_extract_params_extracts_sref():
    assert extract_params("a moody landscape --sref 1234567890") == {"sref": "1234567890"}


def test_extract_params_extracts_aspect_ratio():
    assert extract_params("portrait shot --ar 3:4") == {"ar": "3:4"}


def test_extract_params_extracts_style_keyword():
    assert extract_params("editorial --style raw") == {"style": "raw"}


def test_extract_params_extracts_version():
    assert extract_params("--v 6.1 something") == {"v": "6.1"}


def test_extract_params_extracts_niji():
    assert extract_params("--niji 6") == {"niji": "6"}


def test_extract_params_extracts_numeric_params():
    text = "scene --chaos 30 --stylize 250 --weird 100"
    assert extract_params(text) == {"chaos": "30", "stylize": "250", "weird": "100"}


def test_extract_params_handles_full_realistic_prompt():
    prompt = (
        "minimalist editorial homepage, warm beige and rust palette, "
        "newsprint typography --sref 4072830571 --ar 16:9 --style raw --v 6.1 --stylize 400"
    )
    assert extract_params(prompt) == {
        "sref": "4072830571",
        "ar": "16:9",
        "style": "raw",
        "v": "6.1",
        "stylize": "400",
    }


def test_extract_params_ignores_unknown_flags():
    assert extract_params("--foo bar --sref 123") == {"sref": "123"}


def test_extract_params_tolerates_double_dash_in_url():
    # don't false-positive on `--` inside URLs (no preceding whitespace)
    assert extract_params("https://example.com/--ar/path") == {}
