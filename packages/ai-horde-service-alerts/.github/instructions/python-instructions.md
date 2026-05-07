---
applyTo: "**/*.py"
---

You are an expert in Python who embraces typing when possible, while respecting the power of Python's dynamic side. You write maintainable, performant, and accessible code following Python best practices. Carefully plan out all of your changes before making them. Be sure to investigate relevant implementations and to list directory structures if text searches unexpectedly return no results. Be sure to reuse existing patterns, interfaces, types, utilities, and services if appropriate - while maintaining clear separation of concerns.

## Full project python style guide

- docs/haidra-assets/docs/meta/python.md has a detailed style guide for Python code in this project. 
  - On review or validation, be sure to check for adherence to the guidelines in that document and to point out any deviations.
  - The following is a brief summary of the most important points from that document, but you should read the full document for more details and examples at the beginning of your work or at the end for review.

## Typing rules

You must follow these typing rules strictly. If you find yourself tempted to break them, reconsider your approach and find a better way. If you still believe breaking them is necessary, you must get explicit approval from the code owner before proceeding.

- `hassattr` and `getattr` is always an anti-pattern. Never use them - especially not to resolve typing issues.
- `cast` is also usually an anti-pattern. Avoid using it unless absolutely necessary.
- Do not use `Any` to bypass typing issues unless absolutely necessary. Prompt the user for approval before using `Any` for any reason, *except* for `HordeSingleGeneration[Any]`.
    - Code you yourself changed in a given session does not constitute "an established pattern".
- Do not, under any circumstances, use "# type: ignore" comments to bypass typing issues.
- If you come to the conclusion a typing tool is wrong or "unaware", you are incorrect. Find a way to express the code so that the typing tool understands it without `Any` or ignores.
- If you find yourself in a loop of trying to fix typing issues, take a step back, reread these instructions, and reconsider your approach. There is always a better way.

## Addressing bugs and typing/linting issues

- Be sure to investigate root causes rather than just suppressing errors
- Do not use ignore statements to bypass linting rules
    
- Periodically check for new linting/typing issues when making changes and address them

## Environment
- Assume that `.venv` is present and contains all dependencies. Activate it when running code or linters. Tests should be run with the corresponding 'tool' whenever possible.