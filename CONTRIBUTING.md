# Contributing

Thanks for your interest. This project is small and pragmatic - keep changes focused.

## Dev setup

```bash
git clone https://github.com/Jinssi/tfreport
cd Terraformer
pip install -e ".[dev]"
pytest -v
```

## Pull requests

- Open an issue first for non-trivial changes.
- Add a test for any behaviour change. Fixtures live in `tests/fixtures/`.
- Run `pytest -v` and `ruff check src tests` before pushing.
- Keep the report **advisory**: never make the parser fail a build.
- Risk rules go in [src/tfreport/risk_rules.py](src/tfreport/risk_rules.py); add a fixture + assertion when you add a rule.

## Release

Maintainers tag `vX.Y.Z` on `main`; the release workflow builds and publishes to PyPI via Trusted Publishing.
