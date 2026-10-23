# Troubleshooting and FAQ
This page addresses frequently asked questions (FAQ) and provides troubleshooting steps for common issues encountered while using Rezztor EAgent.

## Frequently Asked Questions
**1. How do I ask Rezztor EAgent to work directly on my files or dirs?**
- Upload them through the **Files** screen, or place them in the user's workspace at
  `/eagent/usr/users/<user-id>/workdir/`. Each account has its own; files dropped in
  `/eagent/usr` directly are not in anyone's workspace.

**2. When I input something in the chat, nothing happens. What's wrong?**
- Check that the provider has an API key on the **Models** screen (administrators). Without one the application cannot call the model.

**3. I get "Invalid model ID." What does that mean?**
- Verify the **provider** and **model naming**. For example, `openai/gpt-5.3` is correct for OpenRouter, but **incorrect** for the native OpenAI provider, which goes without prefix.

**4. Does ChatGPT Plus include API access?**
- No. ChatGPT Plus does not include API credits. An administrator has to give an OpenAI API key on the **Models** screen.

**5. Where is chat history stored?**
- Per user, at `/eagent/usr/users/<user-id>/chats/` inside the container. There is a
  legacy `/eagent/usr/chats/` from before accounts were isolated; new chats do not go
  there.

**6. How do I integrate open-source models with Rezztor EAgent?**
Refer to the Choosing your LLMs section for configuring local models (Ollama, LM Studio, etc.).

> [!TIP]
> Some LLM providers offer free usage tiers, for example Groq, Mistral, SambaNova, or CometAPI.

**7. How can I make Rezztor EAgent retain memory between sessions?**
It already does. Memory, chats and settings live under `/eagent/usr`, which is a volume,
so they survive restarts and upgrades. What loses them is deleting that volume or
starting a fresh one -- so back it up together with `FLASK_SECRET_KEY`, since the data is
not much use without the key that signs the sessions. See
[deployment.md](../setup/deployment.md).

**8. My secrets disappeared after a backup restore.**
Secrets saved from the WebUI live in `usr/.env`, which a backup includes (inside its encrypted archive). Anything you keep outside `usr/` is not in the backup; copy it manually. A backup cannot be restored without its passphrase, and the passphrase cannot be recovered.

**9. Where can I find more documentation?**
- The other guides in this folder, starting with the [Usage Guide](usage.md). For anything they do not answer, contact Rezztor support; **Settings -> Support bundle** makes the file support will ask for.

**10. How do I adjust API rate limits?**
Use the model rate limit fields in Settings, under the Main Model and Utility
Model sections, to set request, input, and output limits.

**11. My `code_execution_tool` doesn't work, what's wrong?**
- Ensure Docker is installed and running.
- On macOS, grant Docker Desktop access to your project files.
- Verify that the Docker image is updated.

**12. Can Rezztor EAgent interact with external APIs or services?**
Yes. Start with [API Integration](api-integration.md) for one-off services or
[MCP Setup](mcp-setup.md) when the service already has MCP support.

## Troubleshooting

**Installation**
- **Docker Issues:** If Docker containers fail to start, consult the Docker documentation and verify your Docker installation and configuration. On macOS, ensure you've granted Docker access to your project files in Docker Desktop's settings as described in the Installation guide. Verify that the Docker image is updated.
- **Web UI not reachable:** Ensure at least one host port is mapped to container port `8080`. If you used `0:8080`, check the assigned port in Docker Desktop.

**Usage**

- **Terminal commands not executing:** code execution runs in a separate runner
  container. Check it is running (`docker compose ps`), that its log shows sshd
  listening on 2222, and that `RUNNER_AUTHORIZED_KEY` matches the public half of the
  key mounted into the application. The application container itself runs no SSH
  server.
- **Rezztor EAgent not starting after an upgrade:** check the container logs
  rather than repairing the running container. A hand-installed Compose
  deployment is upgraded by loading the new images and setting
  `REZZTOR_VERSION`; there is no registry tag to roll back to. An appliance
  installed from the ISO has an update tool that goes back to the old version
  by itself when the new one does not answer.

* **Error Messages:** Pay close attention to the error messages displayed in the Web UI or terminal.  They often provide valuable clues for diagnosing the issue. Under an error in a chat, administrators can open **Technical details** for what the server said.

* **Performance Issues:** If Rezztor EAgent is slow or unresponsive, it might be due to resource limitations, network latency, or the complexity of your prompts and tasks, especially when using local models.
