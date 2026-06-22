# total-recall
- This Python 3.11 solution contains Python code to replay lab streaming layer (LSL) data from a SQLite database
- Developed with Visual Studio Code
- See [python_setup.md](https://gitlab.balldayton.com/trodabau/lsl-publisher-python/-/blob/master/python_setup.md?ref_type=heads) for setup instructions to install and setup Poetry as well as configure VS Code if desired
- The project is mainly intended for testing & development purposes to quickly stand up LSL outlets and publish a lot of data
- Note: if you have trouble getting this to run on Linux and receive an error about the LSL library being missing, download and install the [appropriate liblsl release](https://github.com/sccn/liblsl/releases). 

## Background

- This project is being developed for Dr. Evan Anderson (RHBCN) of AFRL: evan.anderson.20@us.af.mil

## Usage

- Just run the main.py file
- Example batch files are provided
- Recommended: use Poetry to run the project
- See batch files for example of using Poetry to execute the project

## Building an executable

Using Poetry to build the executable is recommended. 

- Ensure the project is installed by running the following from the project's root folder: poetry install
- Run the following: `Poetry build`
- A singular executable should be placed in dist/pyinstaller/(platform) along with the config.toml file
- See pyproject.toml to change the build settings
- Uses [PyInstaller plugin for Poetry](https://pypi.org/project/poetry-pyinstaller-plugin/)
- You may need run the following command to install the PyInstaller Plugin for Poetry: 
  - `poetry self update`
  - `poetry self add poetry-pyinstaller-plugin`
- If the build fails in Linux with a message related to an LSL library (.so file), ensure that the environment variables are set appropriately as follows:
  - I added the following to the .bashrc file:
    - `export PYLSL_LIB=/lib/liblsl.so`
    - `export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/lib`

## Features

- See config file for additional options and features

### Configuration files

- Configuration files will default to the below
- Configuration files can be optionally specified on the command line: --config_file=config.toml