# Development manual for Rezztor EAgent
This guide will show you how to setup a local development environment for Rezztor EAgent in a VS Code compatible IDE, including proper debugger.

> [!TIP]
> The short way is in the repository [README](../../README.md): a virtualenv, `pip install -r requirements.txt`,
> then `python run_ui.py`. The Compose stack in `docker/run/` runs the application beside the code-execution runner.
> Agent code does not run in the WebUI process.





> [!WARNING]
> This guide is for developers and contributors. It assumes you have a basic understanding of how to use Git/GitHub, Docker, IDEs and Python.

> [!NOTE]
> - Rezztor EAgent runs in a Docker container, this simplifies installation and ensures unified environment and behavior across systems.
> - Developing and debugging in a container would be complicated though, therefore we use a hybrid approach where the python framework runs on your machine (in VS Code for example) and only connects to a Dockerized instance when it needs to execute code or use other pre-installed functionality like the built-in search engine.


## To follow this guide you will need:
1. VS Code compatible IDE (VS Code, Cursor, Windsurf...)
2. Python environment (Conda, venv, uv...)
3. Docker (Docker Desktop, docker-ce...)
4. (optional) Git/GitHub

> [!NOTE]
> This walkthrough uses VS Code, Conda and Docker Desktop on macOS. Any IDE and any
> environment manager work; the paths are the only thing that differ.


## Step 0: Install required software
- See the list above and install the software required if you don't already have it.
- You can choose your own variants, but Python, Docker and a VS Code compatible IDE are required.
- For Python you can choose your environment manager - base Python venv, Conda, uv...

## Step 1: Clone or download the repository
- Rezztor EAgent is available on GitHub [github.com/Rezztor/netops-eagent](https://github.com/Rezztor/netops-eagent).
- You can download the files using a browser and extract or run `git clone https://github.com/Rezztor/netops-eagent` in your desired directory.

> [!NOTE]
> For example `cd ~/projects && git clone <your-fork-url> rezztor-eagent`, giving a
> project folder of `~/projects/rezztor-eagent`. Substitute your own path throughout.

## Step 2: Open project folder in your IDE
- Plain VS Code is used here so no step is skipped; any variant such as Cursor or
  Windsurf works the same way.
- Rezztor EAgent comes with `.vscode` folder that contains basic setup, recommended extensions, and debugger profiles. These will help us a lot.

1. Open your IDE and open the project folder using `File > Open Folder` and select your project folder.
2. You will probably be prompted to trust the directory, confirm that.
3. You should now have the project open in your IDE

# Step 3: Prepare your IDE:
1. Notice the prompt in lower right corner of the screenshot above to install recommended extensions, this comes from the `.vscode/extensions.json` file. It contains Python language support, debugger and error helper, install them by confirming the popup or manually in Extensions tab of your IDE. These are the extensions mentioned:
```
usernamehw.errorlens
ms-python.debugpy
ms-python.python
```

Now when you select one of the python files in the project, you should see proper Python syntax highlighting and error detection. It should immediately show some errors, because we did not yet install dependencies.

2. Prepare the python environment to run Rezztor EAgent in. This step assumes you have some Python runtime installed. Click the Python version in the lower right corner to list available environments,
   then `+ Create Virtual Environment`. If several environment managers are installed
   you will be asked which to use; either venv or Conda is fine. Choose **Python 3.12**,
   which is the version this project is tested against.

- Your new environment should be automatically activated. If not, select it in the lower right corner. You might need to open a new terminal in VS Code to reflect the changes with `Terminal > New Terminal` or clicking the `+` button in the terminal tab. Your terminal prompt should now start with the environment path, for example
  `(~/projects/rezztor-eagent/.conda)`, which shows it is active.


3. Install dependencies. Run these commands from the project root:

```bash
pip install -r requirements.txt
```

Errors in the code editor caused by missing packages should now be gone. If not, try reloading the window.


## Step 4: Run Rezztor EAgent in the IDE
Great work! Now you should be able to run Rezztor EAgent from your IDE including real-time debugging.
It will not be able to do code execution and few other features requiring the Docker container just yet, but most of the framework will already work.

1. The project is pre-configured for debugging. Go to Debugging tab, select "run_ui.py" and click the green play button (or press F5 by default). The configuration can be found at `.vscode/launch.json`.


The framework will run at the default port 5000. If you open `http://localhost:5000` in your browser and see `ERR_EMPTY_RESPONSE`, don't panic, you may need to select another port. If you need to change the default port, you can add `"--port=5555"` to the args in the `.vscode/launch.json` file or you can create a `.env` file in the root directory and set the `WEB_UI_PORT` variable to the desired port.

You can also set the bind host via `"--host=0.0.0.0"` (or `WEB_UI_HOST=0.0.0.0`).

It may take a while the first time. You should see output like the screenshot below.


After a model provider is configured in Settings, a simple message should get a response.
Some tools like code execution will not work yet because they need to be connected to a Dockerized instance.



## Debugging
- You can try out the debugger already by placing a breakpoint somewhere in the python code.
- Let's open `api/message.py` for example and place a breakpoint at the beginning of the `communicate` function by clicking on the left of the row number. A red dot should appear showing a breakpoint is set.


- Sending a message in the UI now pauses execution at the breakpoint, so runtime
  variables can be inspected, the code stepped through, values modified and the
  instruction pointer moved. No print statements needed.



## Step 5: Run the stack when you need code execution

Some tools need a Linux environment the host checkout does not provide. Build the images from this
repository ([deployment.md](deployment.md), `docker/run/build.txt`) and start the Compose stack:

```bash
cd docker/run && docker compose up -d
```

The WebUI is on the port that compose file publishes. Agent code runs in the runner container, not in
the WebUI process. A development link that forwarded calls from a host process into a container was
removed on 2026-10-11; `helpers/runtime.py` runs the function where it is called.


# Congratulations!

You have successfully set up a complete Rezztor EAgent development environment! You now have:

- A local development instance running in your IDE with full debugging capabilities
- A Compose stack for code execution and the services the host checkout does not provide
- The ability to develop, debug, and test Rezztor EAgent features

You're now ready to contribute to Rezztor EAgent, create custom extensions, or modify the framework to suit your needs. Happy coding!


## Next steps
- See [Create a Small Plugin](../guides/create-plugin.md) before building a new plugin.

- See [Contributing to Rezztor EAgent](../../CONTRIBUTING.md) for contribution basics.

## Configuration via Environment Variables

For development and testing, you can override default settings using the `.env` file with `REZZTOR_SET_` prefixed variables:

```env
# Add to your .env file
REZZTOR_SET_chat_model_provider=ollama
REZZTOR_SET_chat_model_name=llama3.2
REZZTOR_SET_chat_model_api_base=http://localhost:11434
REZZTOR_SET_memory_recall_interval=5
```

These environment variables automatically override the hardcoded defaults in `get_default_settings()` without modifying code. Useful for testing different configurations or multi-environment setups.

## Want to build your docker image?
- See [deployment.md](deployment.md) and `docker/run/build.txt`.
