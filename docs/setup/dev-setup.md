# Development manual for Rezztor EAgent
This guide will show you how to setup a local development environment for Rezztor EAgent in a VS Code compatible IDE, including proper debugger.

> [!TIP]
> The short way: `dev/run.sh` starts the application from your checkout with its own PostgreSQL and code-execution
> runner, and licenses it against the licence server's development stack. See `dev/README.md`.





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


The framework will run at the default port 5000. If you open `http://localhost:5000` in your browser and see `ERR_EMPTY_RESPONSE`, don't panic, you may need to select another port like I did for some reason. If you need to change the default port, you can add `"--port=5555"` to the args in the `.vscode/launch.json` file or you can create a `.env` file in the root directory and set the `WEB_UI_PORT` variable to the desired port.

You can also set the bind host via `"--host=0.0.0.0"` (or `WEB_UI_HOST=0.0.0.0`).

It may take a while the first time. You should see output like the screenshot below. The RFC error is ok for now as we did not yet connect our local development to another instance in docker.


After inserting my API key in settings, my Rezztor EAgent instance works. I can send a simple message and get a response.
Some tools like code execution will not work yet because they need to be connected to a Dockerized instance.



## Debugging
- You can try out the debugger already by placing a breakpoint somewhere in the python code.
- Let's open `python/api/message.py` for example and place a breakpoint at the beginning of the `communicate` function by clicking on the left of the row number. A red dot should appear showing a breakpoint is set.


- Sending a message in the UI now pauses execution at the breakpoint, so runtime
  variables can be inspected, the code stepped through, values modified and the
  instruction pointer moved. No print statements needed.



## Step 5: Run another instance of Rezztor EAgent in Docker
- Some parts of EAgent require standardized linux environment, additional web services and preinstalled binaries that would be unneccessarily complex to set up in a local environment.
- To make development easier, a dockerized instance can run alongside the local one and
  selected calls are forwarded to it over RFC (Remote Function Call), which is HTTP.

1. Build the image and run it with the web port (`80`) mapped. No image is published
for you to pull today, so build it from this repository -- see
[deployment.md](deployment.md) for the commands. Do not map port 22: the application container runs no SSH server.
Optionally map the `/eagent` folder to your local project folder so both instances see
the same source.
For example: port `80` mapped to `8880` on the host, and `/eagent` mapped to your
project folder.



## Step 6: Configure SSH and RFC connection
- The last step is to configure the local development (VS Code) instance and the dockerized instance to communicate with each other. This is very simple and can be done in the settings in the Web UI of both instances.
- In my example the dark themed instance is the VS Code one, the light themed one is the dockerized instance.

1. Open the "Settings" page in the Web UI of your dockerized instance and go in the "Development" section.
2. Set the `RFC Password` field to a new password and save.
3. Open the "Settings" page in the Web UI of your local instance and go in the "Development" section.
4. Set the `RFC Password` to the same value, and the HTTP port to whatever you mapped
   when creating the container -- `8880` in the example above. `RFC Destination URL`
   normally stays `localhost`, since both instances run on the same host.

   There is no SSH port to set. RFC runs over HTTP: the settings are `rfc_url`,
   `rfc_port_http` and `rfc_password`, and the application container has no SSH server
   to reach. Code execution happens in a separate runner container instead.
5. Click save and test by asking your agent to do something in the terminal, like "Get current OS version". It should reach the dockerized instance over RFC and run the command there, answering
with something like "Debian GNU/Linux 12 (bookworm)".

My Dockerized instance:

My VS Code instance:

## RFC Notes (Host IDE + Docker Execution)
Rezztor EAgent runs code inside the container by default. If you are running the framework locally in your IDE but want tools (like code execution) to run in Docker, configure RFC in **Settings -> Development** and point it to a running Rezztor EAgent container. This routes execution through SSH/RFC to the container while keeping the UI and agent loop on your host.


# Congratulations!

You have successfully set up a complete Rezztor EAgent development environment! You now have:

- A local development instance running in your IDE with full debugging capabilities
- A dockerized instance for code execution and system operations
- RFC and SSH communication between both instances
- The ability to develop, debug, and test Rezztor EAgent features seamlessly

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
- You can use the `DockerfileLocal` to build your docker image.
- Navigate to your project root in the terminal and run `docker build -f DockerfileLocal -t rezztor-eagent-local --build-arg CACHE_DATE=$(date +%Y-%m-%d:%H:%M:%S) .`
- The `CACHE_DATE` argument is optional, it is used to cache most of the build process and only rebuild the last steps when the files or dependencies change.
- See `docker/run/build.txt` for more build command examples.
