# Python Environment Setup

I have an aversion to installing software and prefer to run applications in a "portable" manner whenever possible. This allows multiple versions to be "installed" simultaneously and doesn't risk cluttering up the Windows registry. The default version can be manually controlled by setting the environment variables properly. Additionally, removing a specific version simply involves deleting the directory.

## Download/Setup Python

- https://github.com/winpython/winpython/releases
- Download `Winpython64-<version>.0dot.exe`
- Use 7zip to extract the exe to a directory, I use `C:\Portable Apps`
- Add the following two directories to your PATH environment variable:
  - `C:\Portable Apps\<WinPythonExtractDir>\python-<version>.amd64`
  - `C:\Portable Apps\<WinPythonExtractDir>\python-<version>.amd64\Scripts`
  - **Note:** Windows hijacks the `python` command and will attempt to install it from the Microsoft Store. If another version of python is already installed, it's likely that PATH entries already exist to the other version; these entries should be removed from the PATH environment variable.

## Install/Configure poetry

We have adopted poetry to manage dependencies and virtual environments for python projects.

- Open a command prompt terminal and execute the following command:
  - `curl -sSL https://install.python-poetry.org | python`
- Add the following directory to your PATH environment variable:
  - `%APPDATA%\Python\Scripts`
- Open a new command prompt terminal and execute the following command:
  - `poetry config virtualenvs.in-project true`
    - Creates `.venv` dirs in the project directory; otherwise puts venvs in %APPDATA%.
    - This is a global setting and only needs set once.

## Create a new poetry project

We use a series of utilities to assist with common style/formatting, PEP compliance, static code analysis, test coverage. These should be installed for every python project.

- `pytest` - framework for creating/running automated/unit tests.
- `pytest-cov` - computes code coverage of Python scripts.
- `ruff` - linter, formatter. (replaces black, isort, flake8, pep8-naming, and more).
- `mypy` - static type checker for Python (requires use of type hints in source code).
- `pip-audit` - checks package dependencies for known security vulnerabilities.

Example for setting up a new python project:

- `cd <projectsdir>`
- `poetry new my-new-project`
- `cd my-new-project`
- `poetry install` (creates .venv and installs the package locally)
- `poetry add --group=dev pytest pytest-cov ruff mypy pip-audit`
- Add the following settings/parameters for the tools in `pyproject.toml` :

Suggested configuration/settings for the above tools in `pyproject.toml`

```plaintext
[tool.ruff]
line-length = 120
show-fixes = true

[tool.ruff.lint]
extend-select = [
    "I",     # isort
    "N",     # naming conventions
    "UP",    # pyupgrade -- catches obsolete code patterns
    "ANN",   # type annotations
    "ASYNC", # async code checks
    "B",     # flake8-bugbear -- catches bug-prone usage
    "C4",    # list comprehensions
    "DTZ",   # datetime mistakes -- also please use pendulum instead of datetime
    "INP",   # checks for presence of __init__.py
    "TID",   # tidy imports
    "PTH",   # use pathlib instead of os.path
    "PLW",   # pylint warnings
    "PLE",   # pylint errors
]
ignore = [
    "E501",   # long lines after auto-formatting, such as long strings, are okay
    "ANN002", # we don't demand a type on *args, particularly because it's difficult to specify
    "ANN003", # we don't demand a type on **kwargs, particularly because it's difficult to specify
    "ANN101", # self does not need a type
    "ANN102", # `cls` in classmethod does not need a type
    "ANN401", # Allow "typing.Any"
    "UP007",  # using Optional[X] instead of X | None is okay for now
    "N818",   # Don't require Exception class names to end with "Error"
    "PTH123", # we don't need to construct a Path just to open a file by filename
]

[tool.mypy]
strict = true
show_error_codes = true
no_strict_optional = true
allow_any_generics = true  # allow ': dict' instead of ': dict[Any, Any]'
```

## Installing/Running an existing poetry-managed project

- Clone the repo
- `cd <project>`
- `poetry install` (creates .venv and installs the package locally)

## Visual Studio Code (aka "VSCode" or just "Code")

You are free to use PyCharm or any other editor/IDE of your choice. I prefer/use VSCode, so here are some tips:

- Download: https://code.visualstudio.com/download
  - Per the introduction sentence of this document, I prefer to download the `.zip` installer of VSCode and extract it into `C:\Portable Apps`
- Launch VSCode: `C:\Portable Apps\VSCode-win32-x64-<version>\Code.exe`
- Install the python (and supporting) extensions: `CTRL +P; ext install ms-python.python charliermarsh.ruff` or choose/install the Python (Microsoft) from the extensions pane in VSCode.
- When you open the project directory in VSCode, it should automatically detect the python environment in `.venv`.
- Create the following file `<project_dir>/.vscode/settings.json` to match settings in `pyproject.toml`:

```plaintext
{
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "charliermarsh.ruff",
    "python.testing.pytestArgs": [
        "tests"
    ],
    "editor.codeActionsOnSave": {
        "source.organizeImports": "explicit"
    },
    "python.testing.unittestEnabled": false,
    "python.testing.pytestEnabled": true
}
```