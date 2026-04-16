# Security and Privacy

This project is intended for local use with Bluetooth Low Energy devices that you own or have permission to inspect.

Do not commit local runtime data, virtual environments, logs, cache folders, or environment files. The repository's `.gitignore` excludes those paths by default.

The tracker does not require API keys, cloud credentials, accounts, or remote services. If you add integrations later, keep secrets in environment variables or a local `.env` file and do not commit them.

Bluetooth device names and addresses can identify personal devices. Treat scan output and CSV logs as private local data.
