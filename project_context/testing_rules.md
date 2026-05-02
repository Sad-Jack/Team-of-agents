# Testing Rules

- Use Python `unittest`.
- Tests must not call real OpenAI APIs.
- Fake provider is the default test provider.
- Run test suite with:
  - `python -m unittest discover -s tests`
